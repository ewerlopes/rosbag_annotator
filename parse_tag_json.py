#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import copy
import rosbag
import traceback
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from annotator_utils import *
from collections import defaultdict, OrderedDict
from operator import itemgetter

### logging setup #####
logger = logging.getLogger(__name__)
handler = QtHandler()
format = '%(asctime)s -- %(levelname)s --> %(message)s'
date_format = '%Y-%m-%d %H:%M:%S'
handler.setFormatter(logging.Formatter(format,date_format))
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

assertEnum = {True:"OK",False:"FAILED"}

class AnnotationParser(QWidget):
    def __init__(self, parent=None):
        super(AnnotationParser, self).__init__(parent)
        self.filename = ''

        # the jason config data for setting labels
        self.bag = ''
        self.annotationDictionary = {}     # Topics present on the annotated file.
        self.topicSelection = {}           # This loads the type of objects in the treeviewer. Used for saying which topic to save.
        self.topicSelectionON = defaultdict(list)         # holds which items in the tree of topics is ON
        self.toFileMap        = {}
        self.topicSelectionONHeaders = []
        self.annotationFileName = ''
        self.windowsInterval = []          # stores the start and end times for the windows.
        self.bagFileName = ''
        self.isAnnotationReady = False
        self.isBagReady = False
        self.bag_topics = {}               # topics as extracted from the bag info.
        self.bag_data   = {}               # as a list extracted from the bag_topics
        self.csv_writers = {}              # the csv file objects
        self.output_filenames = {}        # the data file names.
        self.TOLERANCE = 0.001             #tolerance value for deviation in the windows slice


        # Create Push Buttons
        self.openButton = QPushButton("Open Bag")
        self.openButton.clicked.connect(self.openBagFile)
        self.openButton.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.openButton.setMinimumWidth(250)

        self.loadTagJsonButton = QPushButton("Load annotation")
        self.loadTagJsonButton.clicked.connect(self.openAnnotationFile)
        self.loadTagJsonButton.setIcon(self.style().standardIcon(QStyle.SP_FileIcon))
        self.loadTagJsonButton.setMaximumWidth(350)


        self.saveButton = QPushButton("Save")
        self.saveButton.setEnabled(False)
        self.saveButton.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.saveButton.setMaximumWidth(150)
        self.saveButton.clicked.connect(self.save)

        self.tree_of_topics = QTreeWidget()
        self.tree_of_topics.setHeaderLabel("Topics")

        # Create a labels
        self.logOutput_label = QLabel("Log area:")
        self.save_label = QLabel("Saving to:")

        # Create text area for files loaded.
        self.bagFileTextArea = QTextEdit()
        self.bagFileTextArea.setReadOnly(True)
        self.bagFileTextArea.setLineWrapMode(QTextEdit.NoWrap)
        self.bagFileTextArea.setMaximumHeight(50)

        # Save text area
        self.saveTextArea = QTextEdit()
        self.saveTextArea.setReadOnly(True)
        self.saveTextArea.setLineWrapMode(QTextEdit.NoWrap)
        self.saveTextArea.setMaximumHeight(50)

        # Create log area
        self.annotationFileTextArea = QTextEdit()
        self.annotationFileTextArea.setReadOnly(True)
        self.annotationFileTextArea.setLineWrapMode(QTextEdit.NoWrap)
        self.annotationFileTextArea.setMaximumHeight(50)

        # Create log area
        self.logOutput = QTextEdit()
        self.logOutput.setReadOnly(True)
        self.logOutput.setLineWrapMode(QTextEdit.WidgetWidth)

        self.log_font = self.logOutput.font()
        self.log_font.setFamily("Courier")
        self.log_font.setPointSize(10)

        self.logOutput.moveCursor(QTextCursor.End)
        self.logOutput.setCurrentFont(self.log_font)
        self.logOutput.setTextColor(QColor("red"))

        self.scroll_bar = self.logOutput.verticalScrollBar()
        self.scroll_bar.setValue(self.scroll_bar.maximum())

        XStream.stdout().messageWritten.connect(self.logOutput.append)
        XStream.stderr().messageWritten.connect(self.logOutput.append)

        self.topics_to_save = {}


        body_layout = QVBoxLayout()
        self.control_layout1 = QHBoxLayout()
        self.control_layout2 = QHBoxLayout()
        self.control_layout1.addWidget(self.openButton)
        self.control_layout1.addWidget(self.bagFileTextArea)
        self.control_layout2.addWidget(self.loadTagJsonButton)
        self.control_layout2.addWidget(self.annotationFileTextArea)

        body_layout.addLayout(self.control_layout1)
        body_layout.addLayout(self.control_layout2)
        body_layout.addWidget(self.tree_of_topics)
        body_layout.addWidget(self.saveButton)
        body_layout.addWidget(self.save_label)
        body_layout.addWidget(self.saveTextArea)
        body_layout.addWidget(self.logOutput_label)
        body_layout.addWidget(self.logOutput)
        self.setLayout(body_layout)

    def addToTree(self, tree, dictionary):
        if isinstance(dictionary, dict):
            for k, v in dictionary.iteritems():
                if v == []:
                    child = QTreeWidgetItem(tree)
                    child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                    child.setText(0,k)
                    child.setCheckState(0, Qt.Unchecked)
                else:
                    parent = QTreeWidgetItem(tree)
                    parent.setText(0, k)
                    parent.setFlags(parent.flags() | Qt.ItemIsTristate | Qt.ItemIsUserCheckable)
                    parent.setFlags(parent.flags() | Qt.ItemIsTristate)
                    self.addToTree(parent, v)

    def _getTreeSelection(self, subroot, dictionary):
        if subroot.childCount():
            for i in range(subroot.childCount()):
                parent = subroot.child(i)
                newDict = {}
                dictionary[parent.text(0)] = self._getTreeSelection(parent, newDict)
        else:
            if subroot.checkState(0) == QtCore.Qt.Checked:
                dictionary = "ON"
            else:
                dictionary = "OFF"
        return dictionary

    def _flatten_dict(self,dd, separator='.', prefix=''):
        return {prefix + separator + k if prefix else k: v
                for kk, vv in dd.items()
                for k, v in self._flatten_dict(vv, separator, kk).items()
                } if isinstance(dd, dict) else {prefix: dd}

    def processTreeOfTopics(self):
        self.topicSelection = self._flatten_dict(
                                    self._getTreeSelection(self.tree_of_topics.invisibleRootItem(),
                                                          self.topicSelection))
        for k,v in self.topicSelection.iteritems():
            if v == "ON":
                selectionParts = k.split(".")
                featureName = selectionParts[0]
                self.topicSelectionON[featureName].append(".".join(selectionParts[1:]))
                self.topicSelectionONHeaders.append(k)

        logger.info("Tree selection: \n" + json.dumps(self.topicSelection, indent=4, sort_keys=True))
        logger.debug("Selected (ON): "+ json.dumps(self.topicSelectionON,
                                                   indent=4, sort_keys=True))
        logger.debug("Selected (ON) Headers: " + json.dumps(self.topicSelectionONHeaders,
                                                    indent=4, sort_keys=True))

    def treeHasItemSelected(self):
        hasOne = False
        for k,v in self.topicSelection.iteritems():
            if v == "ON":
                hasOne = True
                break
        return hasOne


    def isEnableSave(self):
        return self.isBagReady and self.isAnnotationReady

    def openAnnotationFile(self):
        self.annotationFileName, _ = QFileDialog.getOpenFileName(self, "Open", QDir.currentPath(), "*.json")
        if self.annotationFileName != '':
            try:
                self.annotationDictionary = self.parseJson(self.annotationFileName)
                self.annotationFileTextArea.setText(self.annotationFileName)
                self.addToTree(self.tree_of_topics, self.annotationDictionary["topics"])
            except:
                self.errorMessages(5)

            if self.mustCheckCompatibility():
                if self.areFileCompatible():
                    self._setAnnotationFlags()
                else:
                    self.errorMessages(1)
                    logger.error("Could not load" + self.annotationFileName + " annotation file! "
                                 "Reason: bag is incompatible with the given annotation file.")
                    self.annotationFileName = ''
            else:
                self._setAnnotationFlags()


    def _setAnnotationFlags(self):
        self.loadWindowsTime()
        self.isAnnotationReady = True
        if self.isEnableSave():
            self.saveButton.setEnabled(True)

    def _setBagFlags(self):
        self.bagFileTextArea.setText(self.bagFileName)
        self.isBagReady = True
        if self.isEnableSave():
            self.saveButton.setEnabled(True)

    def loadWindowsTime(self):
        str_buffer = ["\nLOADED WINDOWS INTERVAL"]
        for i,w in enumerate(self.annotationDictionary["windows_interval"]):
            self.windowsInterval.append((w[0],w[1]))
            str_buffer.append("\t#"+str(i)+" - Start: " + str(w[0]) + "secs\t|\tEnd:" + str(w[1]) + "secs")
        total_bag_time = self.annotationDictionary["duration"]
        total_win_time = self.annotationDictionary["windows_interval"]                                      \
                                                  [len(self.annotationDictionary["windows_interval"])-1]    \
                                                  [1]
        str_buffer.append("TOTAL BAG DURATION: "+ str(total_bag_time))
        str_buffer.append("TOTAL WINDOWING TIME: "+str(total_win_time))
        str_buffer.append("TIME NOT USED: " + str(float((total_bag_time)-float(total_win_time))))
        logger.info("\n".join(str_buffer))


    def openBagFile(self):
        self.bagFileName, _ = QFileDialog.getOpenFileName(self, "Open Bag", QDir.currentPath(), "*.bag")
        if self.bagFileName != '':
            try:
                self.bag = rosbag.Bag(self.bagFileName)
                info_dict = yaml.load(self.bag._get_yaml_info())
                self.bag_topics = info_dict['topics']

                string_buffer = []
                string_buffer.append("\nTOPICS FOUND:\n")
                # TODO: try catch the case where there's no topics, currently a potential fatal error.
                for top in self.bag_topics:
                    string_buffer.append("\t- " + top["topic"] + "\n\t\t-Type: " +
                                         top["type"] + "\n\t\t-Fps: " + str(top["frequency"]))

                logger.info("\n".join(string_buffer))
            except Exception,e:
                self.errorMessages(4)
                logger.error(str(e))
            if self.mustCheckCompatibility():
                if self.areFileCompatible():
                    self._setBagFlags()
                else:
                    self.errorMessages(0)
                    logger.error("Could not load" + self.bagFileName +" the bag file! "
                                 "Reason: bag is incompatible with the given annotation file.")
                    self.bagFileName = ''
                    self.bag = ''
                    self.bag_data = ''
                    self.bag_topics = ''
            else:
                self._setBagFlags()

    def mustCheckCompatibility(self):
        """A boolean method and return true if both the bag and the annotation jason files
        had been loaded. Returns false otherwise. The idea of creating this function is to
        avoid the use of repetitive checking condition in the code and to allow unordered
        loading of the files. That is, it does not matter each one of the files had been
         loaded first."""
        if self.isBagReady and self.isAnnotationReady:
            return True
        else: return False

    def areFileCompatible(self):
        """Checks if the jason file can be used in the current loaded bag. In other words,
        whether it has the topics the jason file lists under the key 'topics'. Note that
        if the bag file is different from the one the json file holds the tagged data, but
        has the topics, this function is going to be positive to the compatibility. This is
        a potential situation for error. In other words, it allows to use the json file
        created from other bag in a totally different one, given that it has the listed bags."""

        #loops through the list of topic names listed in the jason and returns false
        # when it sees a topic that is not in the current loaded bag file.
        for d in self.annotationDictionary["topics"].keys():
            if d not in [top["topic"] for top in self.bag_topics]:
                return False
        return True


    def getBagData(self):
        """Sets the bag_data dictionary with with the content of the
        loaded bag.
            self.bag_data[topicName]["msg"] : list of msgs in the bag for the
                                              the given topic (topicName).
            self.bag_data[topiName]["s_time"] : time of the first msg in the
                                                bag for the given topic
            self.bag_data[topicName]["time_buffer_secs"] : list of msg arrival times (in secs)
                                                            for the given bag.
        """
        self.bag_data = {}

        for t_name in [top["topic"] for top in self.bag_topics]:
            # define msg structure. See method stringdoc.
            self.bag_data[t_name] = {}
            self.bag_data[t_name]["msg"] = []
            self.bag_data[t_name]["s_time"] = None
            self.bag_data[t_name]["time_buffer_secs"] = []

        # Buffer the images, timestamps from the rosbag
        for topic, msg, t in self.bag.read_messages(topics=[top["topic"] for top in self.bag_topics]):
            try:
                if self.bag_data[topic]["s_time"] == None:
                    self.bag_data[topic]["s_time"] = t      # sets initial time for the topic s_time.


                self.bag_data[topic]["msg"].append(msg)             # append msg
                # append second difference between the current time ant the s_time.
                self.bag_data[topic]["time_buffer_secs"].append(t.to_sec() -
                                                                 self.bag_data[topic]["s_time"].to_sec())
            except:
                logger.debug("Error: " + topic)

    def parseJson(self,filename):
        """Loads a json. Returns its content in a dictionary"""
        with open(filename) as json_file:
                json_data = json.load(json_file)
        return json_data

    def errorMessages(self,index):
        """Defines error messages via index parameter"""
        msgBox = QMessageBox()
        msgBox.setIcon(QMessageBox.Critical)

        if index == 0:
            msgBox.setText("Error: It was not possible to load the bag file!"
                           "Reason: topic incompatibility.")
        elif index == 1:
            msgBox.setText("Error: It was not possible to load the annotation file!"
                           "Reason: topic incompatibility.")
        elif index == 2:
            msgBox.setText("Error: You must select the topics you are interested.")
        elif index == 3:
            msgBox.setText("Error: You must load a bag file and/or an annotation file!")
        elif index == 4:
            msgBox.setText("Error: Error when opening the bag file!")
        elif index == 5:
            msgBox.setText("Error: Error when opening the annotation json file!")
        msgBox.resize(100,40)
        msgBox.exec_()

    def printDataToFile(self):
        """This function loops through the self.bag_data["msg] data list and based on
        the windows division (self.windowsInterval), prints the data the csv file."""

        logger.info("Aligning different time buffers...")
        # getting a combined timeline using the topics timebuffers.
        self.timeline = {}              # combined time line
        self.sorted_timeline = {}       # the sorted combined time line (it is necessary since dicts are unsorted)
        for s_name in self.annotationDictionary["sources"]:
            combined_buffer = {}
            for topicName in self.topicSelectionON.keys():
                # getting a combined timeline for all user selected topics. combined buffer
                # is a dictionary structure that saves the time(in secs) as key and each topic
                # in the given time as values. If two different topics have the same time, they
                # are stored as a list.
                [combined_buffer.setdefault(t,[]).append(topicName)
                 for t in self.bag_data[topicName]["time_buffer_secs"]]

            # saving the current combined buffer for the feature category (tabs)
            self.timeline[s_name] = combined_buffer
            # sorting the combined buffer for easing the following loops.
            self.sorted_timeline[s_name] = sorted(combined_buffer)

        try:
            # For each feature category (tabs)
            for s_name in self.annotationDictionary["sources"]:
                # Loops through all windows.
                for t,w in enumerate(self.windowsInterval):
                    logger.info("Feature Category: "+ s_name + '\tWin#: ' + str(t))
                    # skip empty tag in the jason file.
                    if self.annotationDictionary[s_name]["tags"][t] == []:
                        # print empty row to the output csv file
                        self.csv_writers[s_name].writerows([{}])
                    else:
                        start = w[0]        # start of the windows
                        end = w[1]          # end of the windows
                        buffer = []         # windows content
                        index_s = 0         # windows start index (allowing looping through the self.timeline)
                        index_e = 0         # windows end index (allowing looping through the self.timeline)

                        ##### loops to discover start index
                        for i in range(len(self.sorted_timeline[s_name])):
                            if self.sorted_timeline[s_name][i] >= start:
                                index_s = i     # set windows start index.
                                break           # exit this start index discovering loop.

                        ##### loops, getting the msg data until the windows end endpoint is reached
                        for j in range(index_s,len(self.timeline[s_name])):
                            # loops while the current index is less then or equal to the windows end endpoint
                            if self.sorted_timeline[s_name][j] <= end:
                                index_e = j     # sets the current index for the data.
                                # copy tag data from current window
                                row = copy.copy(self.annotationDictionary[s_name]["tags"][t])
                                # set the current time stamp for the row
                                row["time"] = self.sorted_timeline[s_name][index_e]
                                # calls self._getMsgValue to retrieve the data for each selected topic (topic field).
                                for topicName in self.timeline[s_name][row["time"]]:
                                    # get data. NOTE: the msg vector is aligned with the time_buffer_sec
                                    # for a given topic, this is because they are currently being saved at
                                    # the same time in getBagData() method. So, we only have to discover
                                    # which msg index is associated with the self.sorted_timeline[s_name]
                                    # value at index_e.
                                    row = self._getMsgValue(row,self.bag_data[topicName]["msg"]
                                    [self.bag_data[topicName]["time_buffer_secs"].index(row["time"])],topicName)
                                    # append row to the windows row batch
                                    buffer.append(row)
                            else:
                                break       ### spin the windows, allowing to move on.

                        ##### Checks whether the deviation between the windows "begin"
                        ##### and "end" times is less the tolerance value.
                        try:
                            assert abs(start - self.sorted_timeline[s_name][index_s]) < self.TOLERANCE
                            logger.info("WStart: " + str(start) + " Retrieval start:" +
                                        str(self.sorted_timeline[s_name][index_s]) + " Sync: OK!")
                        except Exception as e:
                            logger.error("Beginning of the windows is out of sync! MustBe: "+
                                         str(start) + "\tWas: " + str(self.sorted_timeline[s_name][index_s]))
                        try:
                            assert abs(end -self.sorted_timeline[s_name][index_e]) < self.TOLERANCE
                            logger.info("WEnd: " + str(end) + " Retrieval end:" +
                                        str(self.sorted_timeline[s_name][index_e]) + " Sync: OK!")
                        except Exception as e:
                            logger.error("End of the windows is out of sync! MustBe: "+
                                         str(end) + "\tWas: " +
                                         str(self.sorted_timeline[s_name][index_e]))

                        ##### Prints the windows content (row batch) to the corresponding (s_name) csv file.
                        self.csv_writers[s_name].writerows(buffer)  #write content to the file
                        self.csv_writers[s_name].writerows([{}])    #write an empty line to mark the end of the windows
                        self.output_filenames[s_name].flush()       #flush data.

        except Exception as e:
            logger.error(traceback.format_exc())


    def _getMsgValue(self, dictionary, msg, parent, ignore = ["header"]):
        """Recursively saves in dictionary the msg values of the topics set on in the
        tree.
            dictionary : a dictionary returned by the function where the key is the name of
                         the topic, followed by its attribute names (all the way down to the
                         primitive one) and the value is the rosbag topic field msg value.
            msg:        the rosbag msg from which to extract the values.
            parent  :   the topic name from which to search the value in the rosbag msg. note
                        that it is common to have nested data types and so, the parent name is
                        used in order to describe the level of the data topic structure the re-
                        cursion is at. For example, a 6-DOF accelerometer data type may have
                        different attributes, like "gyro" and "acc" each of which may have other
                        attributes like "x","y","z".
            ignore  :   the list of ignored attributes in the msg.
        """
        # if the current topic level has attributes (i.e., it is not a primitive time)
        if hasattr(type(msg), '__slots__'):
            # loops through each attribute of this type
            for s in type(msg).__slots__:
                # ignores attribute if it is in the ignore list.
                if s in ignore:
                    continue
                else:
                    # get the attribute msg value.
                    val = msg.__getattribute__(s)
                    # call the current method again to check if the val attribute value
                    # has another attribute members.
                    dictionary = self._getMsgValue(dictionary, val, ".".join([parent, s]))
        else:
            #if the msg is of a primitive type, set it to dictionary.
            dictionary[parent] = msg

        return dictionary

    def save(self):
        """Opens a dialog windows and asks the general filename
        used for saving the data. It generates as much files as those
        defined in the json source field. In other words, one for each tab
        (feature perspective) used for tagging the data."""

        # Gets each topics were signed for saving.
        self.processTreeOfTopics()
        #checks whether the user has loaded the bag and the jason file.
        if self.isEnableSave() and self.treeHasItemSelected():
            # loads the bag file data in the self.bag_data dictionary variable.
            self.getBagData()
            # defaults directory to the one where the parser program is located in
            defaultdir = os.path.dirname(os.path.abspath(__file__))
            # defaults the name of the output file(s) to the name of the bag + "csv".
            defaultname = self.bagFileName.split("/")[-1][:-4] + ".csv"
            # gets the name of the file from windows.
            insertedName = QFileDialog.getSaveFileName(self, 'Save File', defaultdir + "/"
                                                       + defaultname, filter='*.csv')
            # does nothing in case the file name is empty (the user closed the save windows
            # before pressing save button on it)
            if insertedName[0] != '':
                ###### Process the filename
                # removing .csv extension. This is done because we append to the file
                # the name of the feature perpective (tab name in the annotator.py)
                if insertedName[0].endswith(".csv"):
                    filename = insertedName[0][:-4]     #remove .csv
                    self.saveTextArea.setText(filename) #set the "Saved to" text area
                else:
                    # keep the name as it is in case it has no csv extension
                    filename = insertedName[0]
                    # set the "Saved to" text area
                    self.saveTextArea.setText(filename)

                try:
                    #variable that holds the outputfiles for each perspective. Type: dictionary.
                    self.output_filenames = {}
                    #loop through perspectives.
                    for s_name in self.annotationDictionary["sources"]:
                        # append to the filename the feature perspective name
                        filename = filename + "_" + s_name + ".csv"
                        # set output files
                        self.output_filenames[s_name] = open(filename, 'wa')
                        # define the headers for the csv files (variable, column, names).
                        self.csv_writers[s_name] = csv.DictWriter(self.output_filenames[s_name],
                                                                  ["time"] + self.annotationDictionary[s_name]["labels"]
                                                                  + self.topicSelectionONHeaders)
                        # write the headers
                        self.csv_writers[s_name].writeheader()
                        # flush data
                        self.output_filenames[s_name].flush()
                except Exception as e:
                    logger.error(traceback.format_exc())

                # loop through the data printing the windows content.
                self.printDataToFile()

        # If there is no topic selected in the tree of topics before the button is pressed, ask the user
        # to select at least one.
        elif not self.treeHasItemSelected():
            self.errorMessages(2)
        # If there is no file loaded, ask the user to load them.
        elif not self.isEnabled():
            self.errorMessages(3)

    def closeEvent(self,event):
        """Caputes the pressing of the windows exit button (x button)"""
        pass # currently does nothing.



if __name__ == '__main__':
    app = QApplication(sys.argv)

    player = AnnotationParser()
    player.resize(1340, QApplication.desktop().screenGeometry().height())
    player.show()

    sys.exit(app.exec_())

