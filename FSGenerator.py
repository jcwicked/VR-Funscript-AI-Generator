# Import necessary libraries
import time  # For measuring execution time
import os  # For file and directory operations
import cv2  # OpenCV for image and video processing
import json  # For handling JSON data
from tqdm import tqdm  # For progress bars
from ultralytics import YOLO  # YOLO model for object detection
import platform  # For identifying the operating system
import torch  # PyTorch for use of .pt model if not on Apple device
import tkinter as tk  # GUI library for macOS for basic use in our case
from tkinter import filedialog, messagebox, ttk  # here, what was I saying...
from tkinterdnd2 import DND_FILES, TkinterDnD
import threading
from datetime import datetime
import logging
import sys

# Import custom modules and configurations
from params.config import (class_priority_order, class_reverse_match, class_colors,
                           yolo_models, max_frame_height)  # Configuration for class priorities, reverse matching, and colors
from utils.lib_ObjectTracker import ObjectTracker  # Custom object tracking logic
from utils.lib_FunscriptHandler import FunscriptGenerator  # For generating Funscript files
from utils.lib_Visualizer import Visualizer  # For visualizing results
from utils.lib_Debugger import Debugger  # For debugging and logging
from utils.lib_SceneCutsDetect import detect_scene_changes  # For detecting scene changes in videos
from utils.lib_VideoReaderFFmpeg import VideoReaderFFmpeg  # Custom video reader using FFmpeg

# TODO this is a workaround and needs to be fixed properly
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# Define a GlobalState class to manage global variables
class GlobalState:
    def __init__(self):
        # YOLO models
        self.yolo_det_model = ""
        self.yolo_pose_model = ""
        # Video info
        self.video_file = ""
        self.video_fps = 1
        self.frame_start = 0
        self.frame_end = None
        self.current_frame_id = 0
        self.current_frame = None  # actual frame
        self.frame_area = 0
        self.image_y_size = 0
        self.image_x_size = 0
        # Processing State
        self.should_stop = False
        self.processing_start_time = None
        # Attributes
        self.isVR = True
        self.reference_script = ""
        self.offset_x = 0
        # Funscript data
        self.funscript_data = []  # List to store Funscript data
        self.funscript_frames = []
        self.funscript_distances = []
        # App instances and variables
        self.debugger = None
        self.DebugMode = False
        self.debug_record_mode = False
        self.debug_record_duration = 0
        self.LiveDisplayMode = False
        self.video_reader = "FFmpeg"
        self.enhance_lighting = False
        # Funscript Tweaking Variables
        self.boost_enabled = True
        self.boost_up_percent = 10
        self.boost_down_percent = 15
        self.threshold_enabled = True
        self.threshold_low = 10
        self.threshold_high = 90
        self.vw_simplification_enabled = True
        self.vw_factor = 8.0
        self.rounding = 5
        # Batch Processing Variables
        self.batch_queue = []  # List to store queued video files
        self.batch_status = {}  # Dictionary to store status of each file
        self.current_batch_index = -1  # Index of currently processing file
        self.batch_processing = False  # Whether batch processing is active
        # Configure logging (simple setup)
        # Initialize logger
        logging.basicConfig(
            level=logging.INFO,
            format=f"@{self.current_frame_id} - %(levelname)s - %(message)s",  # Log format
            filename="FSGenerator.log",
            filemode="w",
            handlers=[
                logging.FileHandler("FSGenerator.log"),  # Save logs to a file
                logging.StreamHandler(sys.stdout)  # Print logs to the console
            ]
        )
        self.logger = logging.getLogger("GlobalStateLogger")
        self.logger.setLevel(logging.INFO)
        self.logger.info(f"@{self.current_frame_id} - Initiated logger in global_state instance")


# Initialize global state
global_state = GlobalState()

# Define the BoxRecord class to store bounding box information
class BoxRecord:
    def __init__(self, box, conf, cls, class_name, track_id):
        """
        Initialize a BoxRecord object.
        :param box: Bounding box coordinates [x1, y1, x2, y2].
        :param conf: Confidence score of the detection.
        :param cls: Class ID of the detected object.
        :param class_name: Class name of the detected object.
        :param track_id: Track ID for object tracking.
        """
        self.box = box
        self.conf = conf
        self.cls = cls
        self.class_name = class_name
        self.track_id = int(track_id)

    def __iter__(self):
        """
        Make the BoxRecord object iterable.
        :return: An iterator over the box, confidence, class, class name, and track ID.
        """
        return iter((self.box, self.conf, self.cls, self.class_name, self.track_id))

# Define the Result class to store and manage detection results
class Result:
    def __init__(self, image_width):
        """
        Initialize a Result object.
        :param image_width: Width of the image/frame.
        """
        self.frame_data = {}  # Dictionary to store data for each frame
        self.image_width = image_width

    def add_record(self, frame_id, box_record):
        """
        Add a BoxRecord to the frame_data dictionary.
        :param frame_id: The frame ID to which the record belongs.
        :param box_record: The BoxRecord object to add.
        """
        if frame_id in self.frame_data:
            self.frame_data[frame_id].append(box_record)
        else:
            self.frame_data[frame_id] = [box_record]

    def get_boxes(self, frame_id):
        """
        Retrieve and sort bounding boxes for a specific frame.
        :param frame_id: The frame ID to retrieve boxes for.
        :return: A list of sorted bounding boxes.
        """
        itemized_boxes = []
        if frame_id not in self.frame_data:
            return itemized_boxes
        boxes = self.frame_data[frame_id]
        for box, conf, cls, class_name, track_id in boxes:
            itemized_boxes.append((box, conf, cls, class_name, track_id))
        # Sort boxes based on class priority order
        sorted_boxes = sorted(
            itemized_boxes,
            key=lambda x: class_priority_order.get(x[3], 7)  # Default priority is 7 if class not found
        )
        return sorted_boxes

    def get_all_frame_ids(self):
        """
        Get a list of all frame IDs in the frame_data dictionary.
        :return: A list of frame IDs.
        """
        return list(self.frame_data.keys())

def write_dataset(file_path, data):
    """
    Write data to a JSON file.
    :param file_path: The path to the output file.
    :param data: The data to write.
    """
    global_state.logger.info(f"Exporting data...")
    export_start = time.time()
    # If the file already exists, rename it as a backup
    if os.path.exists(file_path):
        os.rename(file_path, file_path + ".bak")
    # Write the data to the file
    with open(file_path, 'w') as f:
        json.dump(data, f)
    export_end = time.time()
    global_state.logger.info(f"Done in {export_end - export_start}.")

def get_yolo_model_path():
    # Check if the device is an Apple device
    if platform.system() == 'Darwin':
        global_state.logger.info(f"Apple device detected, loading {yolo_models[0]} for MPS inference.")
        return yolo_models[0]

    # Check if CUDA is available (for GPU support)
    elif torch.cuda.is_available():
        global_state.logger.info(f"CUDA is available, loading {yolo_models[1]} for GPU inference.")
        return yolo_models[1]

    # Fallback to ONNX model for other platforms without CUDA
    else:
        global_state.logger.warning("CUDA not available, if this is unexpected, please install CUDA and check your version of torch.")
        global_state.logger.info("You might need to install a dependency with the following command (example):")
        global_state.logger.info("pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118")
        global_state.logger.info(f"Falling back to CPU inference, loading {yolo_models[2]}.")
        global_state.logger.warning("WARNING: CPU inference may be slow on some devices.")

        return yolo_models[2]

def extract_yolo_data(progress_callback=None):
    """
    Extract YOLO detection data from a video.
    Progress updates now use the consolidated update_progress function.
    """
    if os.path.exists(global_state.video_file[:-4] + f"_rawyolo.json"):
        # messagebox to ask if user wants to overwrite or reuse
        # file name without path
        file_name = os.path.basename(global_state.video_file[:-4] + f"_rawyolo.json")
        skip_detection = messagebox.askyesno("Detection file already exists",
                                             f"File {file_name} already exists.\n\nClick Yes to reuse the existing detections file.\nClick No to perform detections again.")
        if skip_detection:
            global_state.logger.info(
                f"File {global_state.video_file[:-4] + f'_rawyolo.json'} already exists. Skipping detections and loading file content...")
            return
        else:
            os.remove(global_state.video_file[:-4] + f"_rawyolo.json")

    records = []  # List to store detection records
    test_result = Result(320)  # Test result object for debugging

    # Initialize the video reader
    cap = VideoReaderFFmpeg(global_state.video_file, is_VR=global_state.isVR)  # Initialize the video reader
    cap.set(cv2.CAP_PROP_POS_FRAMES, global_state.frame_start)

    # Determine the last frame to process
    if global_state.frame_end:
        last_frame = global_state.frame_end
    else:
        last_frame = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Load the YOLO model
    det_model = YOLO(global_state.yolo_det_model, task="detect")

    # make the pose model optional
    if len(global_state.yolo_pose_model) > 0:
        run_pose_model = True
        global_state.logger.info("Activating pose model")
    else:
        run_pose_model = False
        global_state.logger.info("Discarding pose model part of the code")
    if run_pose_model:
        pose_model = YOLO(global_state.yolo_pose_model, task="pose")

    # Start time for ETA calculation
    start_time = time.time()

    # Loop through the video frames
    for frame_pos in tqdm(range(global_state.frame_start, last_frame), ncols=None, desc="Performing YOLO detection on frames"):
        success, frame = cap.read()  # Read a frame from the video

        if success:
            # Run YOLO tracking on the frame
            yolo_det_results = det_model.track(frame, persist=True, conf=0.3, verbose=False)
            if run_pose_model:
                yolo_pose_results = pose_model.track(frame, persist=True, conf=0.3, verbose=False)

            if yolo_det_results[0].boxes.id is None:  # Skip if no tracks are found
                continue

            if len(yolo_det_results[0].boxes) == 0 and not global_state.LiveDisplayMode:  # Skip if no boxes are detected
                continue

            ### DETECTION of BODY PARTS
            # Extract track IDs, boxes, classes, and confidence scores
            track_ids = yolo_det_results[0].boxes.id.cpu().tolist()
            boxes = yolo_det_results[0].boxes.xywh.cpu()
            classes = yolo_det_results[0].boxes.cls.cpu().tolist()
            confs = yolo_det_results[0].boxes.conf.cpu().tolist()

            # Process each detection
            for track_id, cls, conf, box in zip(track_ids, classes, confs, boxes):
                track_id = int(track_id)
                x, y, w, h = box.int().tolist()
                x1 = x - w // 2
                y1 = y - h // 2
                x2 = x + w // 2
                y2 = y + h // 2
                # Create a detection record
                record = [frame_pos, int(cls), round(conf, 1), x1, y1, x2, y2, track_id]
                records.append(record)
                if global_state.LiveDisplayMode:
                    # Print and test the record
                    global_state.logger.info(f"Record : {record}")
                    global_state.logger.info(f"For class id: {int(cls)}, getting: {class_reverse_match.get(int(cls), 'unknown')}")
                    test_box = [[x1, y1, x2, y2], round(conf, 1), int(cls), class_reverse_match.get(int(cls), 'unknown'), track_id]
                    global_state.logger.info(f"Test box: {test_box}")
                    test_result.add_record(frame_pos, test_box)

            if run_pose_model:
                ### POSE DETECTION - Hips and wrists
                # Extract track IDs, boxes, classes, and confidence scores
                if len(yolo_pose_results[0].boxes) > 0 and yolo_pose_results[0].boxes.id is not None:
                    pose_track_ids = yolo_pose_results[0].boxes.id.cpu().tolist()

                    # Check if keypoints are detected
                    if yolo_pose_results[0].keypoints is not None:
                        # print("We have keypoints")
                        # pose_keypoints = yolo_pose_results[0].keypoints.cpu()
                        # pose_track_ids = yolo_pose_results[0].boxes.id.cpu().tolist()
                        # pose_boxes = yolo_pose_results[0].boxes.xywh.cpu()
                        # pose_classes = yolo_pose_results[0].boxes.cls.cpu().tolist()
                        pose_confs = yolo_pose_results[0].boxes.conf.cpu().tolist()

                        pose_keypoints = yolo_pose_results[0].keypoints.cpu()
                        pose_keypoints_list = pose_keypoints.xy.cpu().tolist()
                        left_hip = pose_keypoints_list[0][11]
                        right_hip = pose_keypoints_list[0][12]

                        middle_x_frame = frame.shape[1] // 2
                        mid_hips = [middle_x_frame, (int(left_hip[1])+ int(right_hip[1]))//2]
                        x1 = mid_hips[0]-5
                        y1 = mid_hips[1]-5
                        x2 = mid_hips[0]+5
                        y2 = mid_hips[1]+5
                        cls = 10  # hips center
                        # print(f"pose_confs: {pose_confs}")
                        conf = pose_confs[0]

                        record = [frame_pos, 10, round(conf, 1), x1, y1, x2, y2, 0]
                        records.append(record)
                        if global_state.LiveDisplayMode:
                            # Print and test the record
                            global_state.logger.info(f"@{frame_pos} - Record : {record}")
                            global_state.logger.info(f"@{frame_pos} - For class id: {int(cls)}, getting: {class_reverse_match.get(int(cls), 'unknown')}")
                            test_box = [[x1, y1, x2, y2], round(conf, 1), int(cls),
                                        class_reverse_match.get(int(cls), 'unknown'), 0]
                            global_state.logger.info(f"Test box: {test_box}")
                            test_result.add_record(frame_pos, test_box)

            if global_state.LiveDisplayMode:
                # Verify the sorted boxes
                sorted_boxes = test_result.get_boxes(frame_pos)
                global_state.logger.info(f"@{frame_pos} - Sorted boxes : {sorted_boxes}")

                frame_display = frame.copy()

                for box in sorted_boxes:
                    color = class_colors.get(box[3])
                    cv2.rectangle(frame_display, (box[0][0], box[0][1]), (box[0][2], box[0][3]), color, 2)
                    cv2.putText(frame_display, f"{box[4]}: {box[3]}", (box[0][0], box[0][1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                cv2.imshow("YOLO11 test boxes Tracking", frame_display)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

        # Update progress using the consolidated progress callback
        if progress_callback:
            progress = ((frame_pos - global_state.frame_start) / (last_frame - global_state.frame_start)) * 100
            progress_callback(progress, "yolo")

    # Write the detection records to a JSON file
    write_dataset(global_state.video_file[:-4] + f"_rawyolo.json", records)
    # Release the video capture object and close the display window
    cap.release()
    cv2.destroyAllWindows()

def load_yolo_data_from_file(file_path):
    """
    Load YOLO data from a JSON file.
    :param file_path: Path to the JSON file.
    :return: The loaded data.
    """
    with open(file_path, 'r') as f:
        data = json.load(f)
        global_state.logger.info(f"Loaded data from {file_path}, length: {len(data)}")
    return data

def make_data_boxes(records, image_x_size):
    """
    Convert YOLO records into BoxRecord objects.
    :param records: List of YOLO detection records.
    :param image_x_size: Width of the image/frame.
    :return: A Result object containing BoxRecord instances.
    """
    result = Result(image_x_size)  # Create a Result instance
    for record in records:
        frame_idx, cls, conf, x1, y1, x2, y2, track_id = record
        box = [x1, y1, x2, y2]
        class_name = class_reverse_match.get(cls, 'unknown')
        box_record = BoxRecord(box, conf, cls, class_name, track_id)
        result.add_record(frame_idx, box_record)
    return result

def analyze_tracking_results(results, image_y_size, progress_callback=None):
    """
    Analyze tracking results and generate Funscript data.
    Progress updates now use the consolidated update_progress function.
    
    Args:
        results: The Result object containing detection data.
        image_y_size: Height of the image/frame.
        progress_callback: Callback function for progress updates using the consolidated system.
    """
    list_of_frames = results.get_all_frame_ids()  # Get all frame IDs with detections
    visualizer = Visualizer()  # Initialize the visualizer

    cap = VideoReaderFFmpeg(global_state.video_file, is_VR=global_state.isVR)  # Initialize the video reader

    fps = cap.get(cv2.CAP_PROP_FPS)  # Get the video's FPS
    nb_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))  # Get the total number of frames

    global_state.frame_area = cap.get(cv2.CAP_PROP_FRAME_WIDTH) * cap.get(cv2.CAP_PROP_FRAME_HEIGHT)

    cuts = []

    if not global_state.frame_start:
        global_state.frame_start = 0

    if not global_state.frame_end:
        global_state.frame_end = nb_frames

    if global_state.LiveDisplayMode:
        cap.set(cv2.CAP_PROP_POS_FRAMES, global_state.frame_start)
    else:
        cap.release()

    """ discarding the scene detection for now
    # Load scene cuts if the file exists
    if os.path.exists(global_state.video_file[:-4] + f"_cuts.json"):
        global_state.logger.info(f"Loading cuts from {global_state.video_file[:-4] + f'_cuts.json'}")
        with open(global_state.video_file[:-4] + f"_cuts.json", 'r') as f:
            cuts = json.load(f)
        global_state.logger.info(f"Loaded {len(cuts)} cuts : {cuts}")
    else:
        # Detect scene changes if the cuts file does not exist
        scene_list = detect_scene_changes(global_state.video_file, global_state.isVR, 0.9, global_state.frame_start, global_state.frame_end)
        global_state.logger.info(f"Analyzing frames {global_state.frame_start} to {global_state.frame_end}")
        cuts = [scene[1] for scene in scene_list]
        cuts = cuts[:-1]  # Remove the last entry
        # Save the cuts to a file
        with open(global_state.video_file[:-4] + f"_cuts.json", 'w') as f:
            json.dump(cuts, f)
    """

    global_state.funscript_frames = []  # List to store Funscript frames
    tracker = ObjectTracker(global_state)

    total_frames = global_state.frame_end - global_state.frame_start

    for frame_pos in tqdm(range(global_state.frame_start, global_state.frame_end), unit="f"):
        global_state.current_frame_id = frame_pos
        if frame_pos in cuts:
            # Reinitialize the tracker at scene cuts
            global_state.logger.info(f"@{frame_pos} - Reaching cut")
            previous_distances = tracker.previous_distances
            global_state.logger.info(f"@{frame_pos} - Reinitializing tracker with previous distances: {previous_distances}")
            tracker = ObjectTracker(global_state)
            tracker.previous_distances = previous_distances

        if frame_pos in list_of_frames:
            # Get sorted boxes for the current frame
            sorted_boxes = results.get_boxes(frame_pos)
            tracker.tracking_logic(global_state, sorted_boxes)  # Apply tracking logic

            if tracker.distance:
                # Append Funscript data if distance is available
                global_state.funscript_frames.append(frame_pos)
                global_state.funscript_distances.append(int(tracker.distance))

            if global_state.DebugMode:
                # Log debugging information
                bounding_boxes = []
                for box in sorted_boxes:
                    if box[4] in tracker.normalized_absolute_tracked_positions:
                        if box[4] == 0:  # generic track_id for 'hips center'
                            str_dist_penis = 'None'
                        else:
                            if box[4] in tracker.normalized_distance_to_penis:
                                str_dist_penis = str(int(tracker.normalized_distance_to_penis[box[4]][-1]))
                            else:
                                str_dist_penis = 'None'
                        str_abs_pos = str(int(tracker.normalized_absolute_tracked_positions[box[4]][-1]))
                        position = 'p: ' + str_dist_penis + ' | ' + 'a: ' + str_abs_pos
                        if box[4] in tracker.pct_weights:
                            if len(tracker.pct_weights[box[4]]) > 0:
                                weight = tracker.pct_weights[box[4]][-1]
                                position += ' | w: ' + str(weight)
                    else:
                        position = None
                    bounding_boxes.append({
                        'box': box[0],
                        'conf': box[1],
                        'class_name': box[3],
                        'track_id': box[4],
                        'position': position,
                    })
                global_state.debugger.log_frame(frame_pos,
                                   bounding_boxes=bounding_boxes,
                                   variables={
                                       'frame': frame_pos,
                                       'time': datetime.fromtimestamp(frame_pos / fps).strftime('%H:%M:%S'),
                                       'distance': tracker.distance,
                                       'Penetration': tracker.penetration,
                                       'sex_position': tracker.sex_position,
                                       'sex_position_reason': tracker.sex_position_reason,
                                       'tracked_body_part': tracker.tracked_body_part,
                                       'locked_penis_box': tracker.locked_penis_box.to_dict(),
                                       'glans_detected': tracker.glans_detected,
                                       'cons._glans_detections': tracker.consecutive_detections['glans'],
                                       'cons._glans_non_detections': tracker.consecutive_non_detections['glans'],
                                       'cons._penis_detections': tracker.consecutive_detections['penis'],
                                       'cons._penis_non_detections': tracker.consecutive_non_detections['penis'],
                                       'breast_tracking': tracker.breast_tracking,
                                   })

        if global_state.LiveDisplayMode:
            # Display the tracking results for testing
            ret, frame = cap.read()

            frame_display = frame.copy()

            for box in tracker.tracked_boxes:
                frame_display = visualizer.draw_bounding_box(frame_display,
                                                             box[0],
                                                             str(box[2]) + ": " + box[1],
                                                             class_colors[str(box[1])],
                                                             global_state.offset_x)
            if tracker.locked_penis_box is not None and tracker.locked_penis_box.is_active():
                frame_display = visualizer.draw_bounding_box(frame_display, tracker.locked_penis_box.box,
                                                             "Locked_Penis",
                                                             class_colors['penis'],
                                                             global_state.offset_x)
            else:
                global_state.logger.info(f"@{frame_pos} - No active locked penis box to draw.")

            if tracker.glans_detected:
                frame_display = visualizer.draw_bounding_box(frame_display, tracker.boxes['glans'],
                                                              "Glans",
                                                              class_colors['glans'],
                                                              global_state.offset_x)
            if global_state.funscript_distances:
                frame_display = visualizer.draw_gauge(frame_display, global_state.funscript_distances[-1])

            cv2.imshow("Combined Results", frame_display)
            cv2.waitKey(1)

        # Update progress using the consolidated progress callback
        if progress_callback:
            progress = ((frame_pos - global_state.frame_start) / total_frames) * 100
            progress_callback(progress, "tracking")

    # Prepare Funscript data
    global_state.funscript_data = list(zip(global_state.funscript_frames, global_state.funscript_distances))

    points = "["
    for i in range(len(global_state.funscript_frames)):
        if i != 0:
            points += ","
        points += f"[{global_state.funscript_frames[i]}, {global_state.funscript_distances[i]}]"
    points += "]"
    # Write the raw Funscript data to a JSON file
    with open(global_state.video_file[:-4] + f"_rawfunscript.json", 'w') as f:
        json.dump(global_state.funscript_data, f)
    return global_state.funscript_data

def parse_yolo_data_looking_for_penis(data, start_frame):
    """
    Parse YOLO data to find the first instance of a penis.
    :param data: The YOLO detection data.
    :param start_frame: The starting frame for the search.
    :return: The frame ID where the penis is first detected.
    """
    consecutive_frames = 0
    frame_detected = 0
    penis_frame = 0
    for line in data:
        if line[0] >= start_frame and line[1] == 0 and line[2] >= 0.5:
            penis_frame = line[0]
        if line[0] == penis_frame and line[1] == 1 and line[2] >= 0.5:
            if frame_detected == 0:
                frame_detected = line[0]
                consecutive_frames += 1
            elif line[0] == frame_detected + 1:
                consecutive_frames += 1
                frame_detected = line[0]
            else:
                consecutive_frames = 0
                frame_detected = 0

            if consecutive_frames >= 2:
                global_state.logger.info(f"First instance of Glans/Penis found in frame {line[0] - 4}")
                return line[0] - 4

def select_video_file():
    file_path = filedialog.askopenfilename(
        title="Select a video file",
        filetypes=[("Video Files", "*.mp4 *.avi *.mov *.mkv")]
    )
    if file_path:
        video_path.set(file_path)
        check_video_resolution(file_path)

def select_reference_script():
    file_path = filedialog.askopenfilename(
        title="Select a reference funscript file",
        filetypes=[("Funscript Files", "*.funscript")]
    )
    if file_path:
        reference_script_path.set(file_path)

def check_video_resolution(video_path):
    cap = cv2.VideoCapture(video_path)
    global_state.video_fps = float(cap.get(cv2.CAP_PROP_FPS))
    global_state.logger.info(f"Video FPS: {global_state.video_fps}")
    if not cap.isOpened():
        messagebox.showerror("Error", "Could not open the video file.")
        return

    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    if height > max_frame_height:
        messagebox.showinfo("Info", f"The video height is {height}p > {max_frame_height}p.\nIt will be automatically resized on the fly, no conversion required.")

def common_initialization():
    global_state.video_file = video_path.get()
    if not global_state.video_file:
        messagebox.showerror("Error", "Please select a video file.")
        return

    global_state.yolo_det_model = get_yolo_model_path()
    global_state.yolo_pose_model = ""  # "models/yolo11n-pose.mlpackage"
    global_state.DebugMode = debug_mode_var.get()
    global_state.debug_record_mode = debug_record_mode_var.get()
    global_state.debug_record_duration = int(debug_record_duration_var.get())
    global_state.LiveDisplayMode = live_display_mode_var.get()
    selected_mode = mode_combobox.get()
    if selected_mode == "VR SBS":
        global_state.isVR = True
    elif selected_mode == "Flat - 2D POV":  # might want to add other formats later on
        global_state.isVR = False
    else:
        global_state.isVR = False

    global_state.enhance_lighting = enhance_lighting_var.get()
    # Initialize frame start and end to defaults since we removed the entry fields
    global_state.frame_start = 0
    global_state.frame_end = None
    global_state.reference_script = reference_script_path.get()
    global_state.enhance_lighting = enhance_lighting_var.get()

    cap = VideoReaderFFmpeg(global_state.video_file, is_VR=global_state.isVR)
    global_state.image_x_size = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    global_state.image_y_size = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    global_state.video_fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()

    global_state.logger.info(f"Processing video: {global_state.video_file}")
    global_state.logger.info(f"Image size: {global_state.image_x_size}x{global_state.image_y_size}")
    global_state.logger.info(f"FPS: {global_state.video_fps}")
    global_state.logger.info(f"Video Reader: {global_state.video_reader}")
    global_state.logger.info(f"YOLO Detection Model: {global_state.yolo_det_model}")
    global_state.logger.info(f"YOLO Pose Model: {global_state.yolo_pose_model}")
    global_state.logger.info(f"Debug Mode: {global_state.DebugMode}")
    global_state.logger.info(f"Live Display Mode: {global_state.LiveDisplayMode}")
    global_state.logger.info(f"VR Mode: {global_state.isVR}")
    global_state.logger.info(f"Enhance lighting: {global_state.enhance_lighting}")
    global_state.logger.info(f"Frame Start: {global_state.frame_start}")
    global_state.logger.info(f"Frame End: {global_state.frame_end}")
    global_state.logger.info(f"Reference Script: {global_state.reference_script}")
    global_state.logger.info(f"Video Reader: {global_state.video_reader}")
    global_state.logger.info(f"Enhance lighting: {global_state.enhance_lighting}")

def process_video(video_file, funscript_path, progress_callback, complete_callback):
    """
    Process a video file to generate a funscript.
    
    Args:
        video_file: Path to the video file
        funscript_path: Path where the funscript will be saved
        progress_callback: Callback function for progress updates
        complete_callback: Callback function when processing is complete
    """
    try:
        # Set up the global state for this video
        global_state.video_file = video_file
        global_state.processing_start_time = time.time()
        
        # Initialize common settings
        common_initialization()
        
        # Check if processing should stop
        if global_state.should_stop:
            complete_callback()
            return
            
        # Initialize the debugger
        global_state.debugger = Debugger(global_state.video_file, output_dir=global_state.video_file[:-4])
        
        # Run YOLO detection and save result to _rawyolo.json file
        extract_yolo_data(progress_callback)
        
        # Check if processing should stop
        if global_state.should_stop:
            complete_callback()
            return
            
        # Load YOLO detection results from file
        yolo_data = load_yolo_data_from_file(global_state.video_file[:-4] + "_rawyolo.json")
        
        # Convert YOLO data to box format
        results = make_data_boxes(yolo_data, global_state.image_x_size)
        
        # Find first instance of penis
        first_penis_frame = parse_yolo_data_looking_for_penis(yolo_data, 0)
        
        if first_penis_frame is None:
            global_state.logger.error(f"No penis found in video: {global_state.video_file}")
            first_penis_frame = 0
            
        # Adjust frame start
        global_state.frame_start = max(
            max(first_penis_frame - int(global_state.video_fps), 
                global_state.frame_start - int(global_state.video_fps)), 
            0
        )
        
        global_state.logger.info(f"Frame Start adjusted to: {global_state.frame_start}")
        
        # Check if processing should stop
        if global_state.should_stop:
            complete_callback()
            return
            
        # Perform tracking analysis and generate raw funscript data
        global_state.funscript_data = analyze_tracking_results(
            results, 
            global_state.image_y_size,
            progress_callback
        )
        
        # Check if processing should stop
        if global_state.should_stop:
            complete_callback()
            return
            
        # Save debug logs if in debug mode
        if global_state.DebugMode:
            global_state.debugger.save_logs()
        
        # Generate the funscript
        funscript_handler = FunscriptGenerator()
        funscript_handler.generate(global_state)
        
        # Create report if reference script exists
        funscript_handler.create_report_funscripts(global_state)
        
        global_state.logger.info(f"Finished processing video: {global_state.video_file}")
        
        # Call the completion callback
        complete_callback()
        
    except Exception as e:
        global_state.logger.error(f"Error in process_video: {str(e)}")
        messagebox.showerror("Error", f"Error processing video: {str(e)}")
        complete_callback()  # Still call complete_callback to ensure proper cleanup

def start_processing():
    """
    Start processing a video file. This function has been simplified to use the process_video function
    which now contains the core processing logic. This avoids code duplication and centralizes the 
    processing workflow.
    """
    if not video_path.get():
        messagebox.showerror("Error", "Please select a video file first")
        if global_state.batch_processing:
            process_next_in_queue()
        return

    # Disable the start button during processing
    start_button.configure(state="disabled")
    
    try:
        # Create output directory if it doesn't exist
        output_dir = os.path.join(os.path.dirname(video_path.get()), "output")
        os.makedirs(output_dir, exist_ok=True)

        # Get the base filename without extension
        base_filename = os.path.splitext(os.path.basename(video_path.get()))[0]
        
        # Create the funscript file path
        funscript_path = os.path.join(output_dir, f"{base_filename}.funscript")
        
        # Start processing in a separate thread
        processing_thread = threading.Thread(
            target=process_video,
            args=(video_path.get(), funscript_path, update_progress, processing_complete)
        )
        processing_thread.start()
        
    except Exception as e:
        messagebox.showerror("Error", f"Failed to start processing: {str(e)}")
        start_button.configure(state="normal")
        if global_state.batch_processing:
            process_next_in_queue()

def processing_complete():
    start_button.configure(state="normal")
    if global_state.batch_processing:
        current_file = global_state.batch_queue[global_state.current_batch_index]
        global_state.batch_status[current_file] = "Complete"
        update_batch_list()
        process_next_in_queue()
    else:
        messagebox.showinfo("Success", "Processing complete!")

def debug_function():
    """
    Debugging function to perform specific debugging tasks.
    """
    common_initialization()

    # Processing logic

    global_state.debugger = Debugger(global_state.video_file, global_state.isVR, global_state.video_reader, output_dir=global_state.video_file[:-4])  # Initialize the debugger

    # if the debug_logs.json file exists, load it
    if os.path.exists(global_state.video_file[:-4] + f"_debug_logs.json"):
        global_state.debugger.load_logs()
        global_state.debugger.play_video(start_frame=global_state.frame_start,
                                         duration=global_state.debug_record_duration if global_state.debug_record_mode else 0,
                                         record=global_state.debug_record_mode,
                                         downsize_ratio=2)
    else:
        messagebox.showinfo("Info", f"Debug logs file not found: {global_state.video_file[:-4] + f'_debug_logs.json'}")

def regenerate_funscript(global_state):
    global_state.video_file = video_path.get()
    if not global_state.video_file:
        messagebox.showerror("Error", "Please select a video file.")
        return
    global_state.reference_script = reference_script_path.get()

    global_state.logger.info("Regenerating Funscript with tweaked settings...")
    # Apply tweaks to funscript_data
    if global_state.boost_enabled:
        global_state.logger.info(f"Applying Boost: Up {global_state.boost_up_percent}%, Down {global_state.boost_down_percent}%")
        # Add boost logic here

    if global_state.threshold_enabled:
        global_state.logger.info(f"Applying Threshold: Low {global_state.threshold_low}, High {global_state.threshold_high}")
        # Add threshold logic here

    if global_state.vw_simplification_enabled:
        global_state.logger.info(f"Applying VW Simplification with Factor: {global_state.vw_factor} then rounding to {global_state.rounding}")
        # Add VW simplification logic here

    # Save and regenerate funscript
    funscript_handler = FunscriptGenerator()
    # Simplifying the funscript data and generating the file
    funscript_handler.generate(global_state)
    global_state.logger.info("Funscript re-generation complete.")
    # Optional, compare generated funscript with reference funscript if specified, or a simple generic report
    funscript_handler.create_report_funscripts(global_state)

    global_state.logger.info("Report generation complete.")


def quit_application():
    """
    Quit the application.
    """
    global_state.logger.info("Quitting the application...")
    root.quit()  # Close the Tkinter main loop
    root.destroy()  # Destroy the root window


# GUI Setup
root = TkinterDnD.Tk()  # Use TkinterDnD.Tk instead of tk.Tk
root.title("VR funscript AI Generator")

# Variables
video_path = tk.StringVar()
reference_script_path = tk.StringVar()
debug_mode_var = tk.BooleanVar(value=True)  # Default to True
debug_record_mode_var = tk.BooleanVar(value=False)
debug_record_duration_var = tk.StringVar(value="5")  # Default duration
live_display_mode_var = tk.BooleanVar(value=False)
enhance_lighting_var = tk.BooleanVar(value=False)

# Funscript Tweaking Variables
boost_enabled_var = tk.BooleanVar(value=True)  # Default to True
boost_up_percent_var = tk.IntVar(value=10)  # Default 10%
boost_down_percent_var = tk.IntVar(value=15)  # Default 15%
threshold_enabled_var = tk.BooleanVar(value=True)  # Default to True
threshold_low_var = tk.IntVar(value=10)  # Default 10
threshold_high_var = tk.IntVar(value=90)  # Default 90
vw_simplification_enabled_var = tk.BooleanVar(value=True)  # Default to True
vw_factor_var = tk.DoubleVar(value=8.0)  # Default 8.0

# Video File Selection
video_frame = ttk.LabelFrame(root, text="Video Selection", padding=(10, 5))
video_frame.grid(row=0, column=0, columnspan=3, padx=5, pady=5, sticky="ew")

ttk.Label(video_frame, text="Video File:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
ttk.Entry(video_frame, textvariable=video_path, width=50).grid(row=0, column=1, padx=5, pady=5)
ttk.Button(video_frame, text="Browse", command=select_video_file).grid(row=0, column=2, padx=5, pady=5, sticky="e")

mode_label = ttk.Label(video_frame, text="Select Video Mode:")
mode_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")

mode_combobox = ttk.Combobox(video_frame, values=["VR SBS", "Flat - 2D POV"], state="readonly")
mode_combobox.grid(row=2, column=1, padx=5, pady=5, sticky="w")
mode_combobox.set("VR SBS")  # Set default value

# Processing Mode Selection
processing_frame = ttk.LabelFrame(root, text="Processing", padding=(10, 5))
processing_frame.grid(row=1, column=0, columnspan=3, padx=5, pady=5, sticky="ew")

start_button = ttk.Button(processing_frame, text="Start Processing", command=start_processing)
start_button.grid(row=0, column=0, padx=5, pady=5, sticky="w")

ttk.Checkbutton(processing_frame, text="Logging for debug", variable=debug_mode_var).grid(row=0, column=2, padx=5, pady=5)
debug_mode_var.set(True)
# this one needs a fix
# ttk.Checkbutton(processing_frame, text="Live inference => slow & heavy!", variable=live_display_mode_var).grid(row=0, column=2, padx=5, pady=5)

# Progress Bar for YOLO Detection
yolo_progress_label = ttk.Label(processing_frame, text="YOLO Detection Progress:")
yolo_progress_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
yolo_progress_bar = ttk.Progressbar(processing_frame, orient="horizontal", length=300, mode="determinate")
yolo_progress_bar.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
yolo_progress_percent = ttk.Label(processing_frame, text="0%", width=20, anchor="w")
yolo_progress_percent.grid(row=1, column=2, padx=5, pady=5, sticky="w")

# Progress Bar for Tracking Analysis
tracking_progress_label = ttk.Label(processing_frame, text="Tracking Analysis Progress:")
tracking_progress_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
tracking_progress_bar = ttk.Progressbar(processing_frame, orient="horizontal", length=300, mode="determinate")
tracking_progress_bar.grid(row=2, column=1, padx=5, pady=5, sticky="ew")
tracking_progress_percent = ttk.Label(processing_frame, text="0%", width=20, anchor="w")
tracking_progress_percent.grid(row=2, column=2, padx=5, pady=5, sticky="w")

# Current Processing Status
current_file_frame = ttk.Frame(processing_frame)
current_file_frame.grid(row=3, column=0, columnspan=3, padx=5, pady=5, sticky="ew")

current_file_label = ttk.Label(current_file_frame, text="Current File:")
current_file_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
current_file_status = ttk.Label(current_file_frame, text="None", wraplength=400)
current_file_status.grid(row=0, column=1, padx=5, pady=5, sticky="w")

# Optional Settings Section
optional_settings = ttk.LabelFrame(root, text="Optional settings", padding=(10, 5))
optional_settings.grid(row=2, column=0, columnspan=3, padx=5, pady=5, sticky="ew")

# Collapse/Expand Button
def toggle_optional_settings():
    if optional_settings_collapsible.winfo_ismapped():
        optional_settings_collapsible.grid_remove()
    else:
        optional_settings_collapsible.grid()

toggle_button = ttk.Button(optional_settings, text="Toggle Optional Settings", command=toggle_optional_settings)
toggle_button.grid(row=0, column=0, padx=5, pady=5, sticky="w")

# Collapsible Section
optional_settings_collapsible = ttk.Frame(optional_settings)
optional_settings_collapsible.grid(row=1, column=0, columnspan=3, padx=5, pady=5, sticky="ew")

ttk.Label(optional_settings_collapsible, text="Frame Start:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
frame_start_entry = ttk.Entry(optional_settings_collapsible, width=10)
frame_start_entry.grid(row=0, column=1, padx=5, pady=5, sticky="w")

ttk.Label(optional_settings_collapsible, text="Frame End:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
frame_end_entry = ttk.Entry(optional_settings_collapsible, width=10)
frame_end_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")

ttk.Label(optional_settings_collapsible, text="Reference Script:").grid(row=2, column=0, padx=5, pady=5)
ttk.Entry(optional_settings_collapsible, textvariable=reference_script_path, width=50).grid(row=2, column=1, padx=5, pady=5)
ttk.Button(optional_settings_collapsible, text="Browse", command=select_reference_script).grid(row=2, column=2, padx=5, pady=5)

ttk.Checkbutton(optional_settings_collapsible, text="Enhance lighting", variable=enhance_lighting_var).grid(row=3, column=0, columnspan=2, padx=5, pady=5, sticky="w")

optional_settings_collapsible.grid_remove()

# Funscript Tweaking Section
funscript_tweaking_frame = ttk.LabelFrame(root, text="Funscript Tweaking", padding=(10, 5))
funscript_tweaking_frame.grid(row=3, column=0, columnspan=3, padx=5, pady=5, sticky="ew")

# Collapse/Expand Button
def toggle_funscript_tweaking():
    if funscript_tweaking_collapsible.winfo_ismapped():
        funscript_tweaking_collapsible.grid_remove()
    else:
        funscript_tweaking_collapsible.grid()

toggle_button = ttk.Button(funscript_tweaking_frame, text="Toggle Funscript Tweaking", command=toggle_funscript_tweaking)
toggle_button.grid(row=0, column=0, padx=5, pady=5, sticky="w")

# Collapsible Section
funscript_tweaking_collapsible = ttk.Frame(funscript_tweaking_frame)
funscript_tweaking_collapsible.grid(row=1, column=0, columnspan=3, padx=5, pady=5, sticky="ew")

# Boost Settings
boost_frame = ttk.LabelFrame(funscript_tweaking_collapsible, text="Boost Settings", padding=(10, 5))
boost_frame.grid(row=1, column=0, padx=5, pady=5, sticky="ew")

boost_checkbox = ttk.Checkbutton(boost_frame, text="Enable Boost", variable=boost_enabled_var, command=lambda: setattr(global_state, 'boost_enabled', not global_state.boost_enabled))
boost_checkbox.grid(row=0, column=0, padx=5, pady=5, sticky="w")
boost_enabled_var.set(global_state.boost_enabled)

ttk.Label(boost_frame, text="Boost Up %:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
boost_up_selector = ttk.Combobox(boost_frame, values=[str(i) for i in range(0, 21)], width=5)
boost_up_selector.set(str(global_state.boost_up_percent))
boost_up_selector.grid(row=1, column=1, padx=5, pady=5, sticky="w")
boost_up_selector.bind("<<ComboboxSelected>>", lambda e: setattr(global_state, 'boost_up_percent', int(boost_up_selector.get())))

ttk.Label(boost_frame, text="Reduce Down %:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
boost_down_selector = ttk.Combobox(boost_frame, values=[str(i) for i in range(0, 21)], width=5)
boost_down_selector.set(str(global_state.boost_down_percent))
boost_down_selector.grid(row=2, column=1, padx=5, pady=5, sticky="w")
boost_down_selector.bind("<<ComboboxSelected>>", lambda e: setattr(global_state, 'boost_down_percent', int(boost_down_selector.get())))

# Threshold Settings
threshold_frame = ttk.LabelFrame(funscript_tweaking_collapsible, text="Threshold Settings", padding=(10, 5))
threshold_frame.grid(row=1, column=1, padx=5, pady=5, sticky="ew")

threshold_checkbox = ttk.Checkbutton(threshold_frame, text="Enable Threshold", variable=threshold_enabled_var, command=lambda: setattr(global_state, 'threshold_enabled', not global_state.threshold_enabled))
threshold_checkbox.grid(row=0, column=0, padx=5, pady=5, sticky="w")
threshold_enabled_var.set(global_state.threshold_enabled)

ttk.Label(threshold_frame, text="0 Threshold:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
threshold_low_selector = ttk.Combobox(threshold_frame, values=[str(i) for i in range(0, 16)], width=5)
threshold_low_selector.set(str(global_state.threshold_low))
threshold_low_selector.grid(row=1, column=1, padx=5, pady=5, sticky="w")
threshold_low_selector.bind("<<ComboboxSelected>>", lambda e: setattr(global_state, 'threshold_low', int(threshold_low_selector.get())))

ttk.Label(threshold_frame, text="100 Threshold:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
threshold_high_selector = ttk.Combobox(threshold_frame, values=[str(i) for i in range(80, 101)], width=5)
threshold_high_selector.set(str(global_state.threshold_high))
threshold_high_selector.grid(row=2, column=1, padx=5, pady=5, sticky="w")
threshold_high_selector.bind("<<ComboboxSelected>>", lambda e: setattr(global_state, 'threshold_high', int(threshold_high_selector.get())))

# Simplification Settings
vw_frame = ttk.LabelFrame(funscript_tweaking_collapsible, text="Simplification", padding=(10, 5))
vw_frame.grid(row=1, column=3, padx=5, pady=5, sticky="ew")

vw_checkbox = ttk.Checkbutton(vw_frame, text="Enable Simplification", variable=vw_simplification_enabled_var, command=lambda: setattr(global_state, 'vw_simplification_enabled', not global_state.vw_simplification_enabled))
vw_checkbox.grid(row=0, column=0, padx=5, pady=5, sticky="w")
vw_simplification_enabled_var.set(global_state.vw_simplification_enabled)

ttk.Label(vw_frame, text="VW Factor:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
vw_factor_selector = ttk.Combobox(vw_frame, values=[str(i / 5) for i in range(10, 51)], width=5)
vw_factor_selector.set(str(global_state.vw_factor))
vw_factor_selector.grid(row=1, column=1, padx=5, pady=5, sticky="w")
vw_factor_selector.bind("<<ComboboxSelected>>", lambda e: setattr(global_state, 'vw_factor', float(vw_factor_selector.get())))

ttk.Label(vw_frame, text="Rounding:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
rounding = ttk.Combobox(vw_frame, values=['5', '10'], width=5)
rounding.set(str(global_state.rounding))
rounding.grid(row=2, column=1, padx=5, pady=5, sticky="w")
rounding.bind("<<ComboboxSelected>>", lambda e: setattr(global_state, 'rounding', float(rounding.get())))

# Regenerate Funscript Button
regenerate_funscript_button = ttk.Button(funscript_tweaking_collapsible, text="Regenerate Funscript", command=lambda: regenerate_funscript(global_state))
regenerate_funscript_button.grid(row=2, column=0, padx=5, pady=5, sticky="w")

funscript_tweaking_collapsible.grid_remove()

# Debug Record Mode
debug_frame = ttk.LabelFrame(root, text="Debugging (Replay and navigate a processed video)", padding=(10, 5))
debug_frame.grid(row=4, column=0, columnspan=3, padx=5, pady=5, sticky="ew")

quit_button = ttk.Button(debug_frame, text="Video (q to quit)", command=debug_function)
quit_button.grid(row=0, column=0, padx=5, pady=5, sticky="w")

ttk.Checkbutton(debug_frame, text="Save debugging session as video", variable=debug_record_mode_var).grid(row=0, column=1, padx=5, pady=5)

# Duration Selector
duration_combobox = ttk.Combobox(debug_frame, textvariable=debug_record_duration_var, values=["5", "10", "20"], width=5)
duration_combobox.grid(row=0, column=2, padx=5, pady=5)
ttk.Label(debug_frame, text="seconds").grid(row=0, column=3, padx=5, pady=5)

# Batch Processing Section
batch_frame = ttk.LabelFrame(root, text="Batch Processing", padding=(10, 5))
batch_frame.grid(row=5, column=0, columnspan=3, padx=5, pady=5, sticky="ew")

# Overall Batch Progress
batch_overall_frame = ttk.Frame(batch_frame)
batch_overall_frame.grid(row=2, column=0, columnspan=3, padx=5, pady=5, sticky="ew")

batch_progress_label = ttk.Label(batch_overall_frame, text="Overall Batch Progress:")
batch_progress_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")

batch_count_label = ttk.Label(batch_overall_frame, text="0/0 files processed")
batch_count_label.grid(row=0, column=1, padx=5, pady=5, sticky="w")

# Create a frame for the batch buttons
batch_button_frame = ttk.Frame(batch_frame)
batch_button_frame.grid(row=0, column=0, columnspan=3, padx=5, pady=5, sticky="ew")

def add_files():
    files = filedialog.askopenfilenames(
        title="Select video files",
        filetypes=[("Video Files", "*.mp4 *.avi *.mov *.mkv")]
    )
    for file in files:
        if file not in global_state.batch_queue:
            global_state.batch_queue.append(file)
            global_state.batch_status[file] = "Queued"
            update_batch_list()

def remove_selected():
    selection = batch_listbox.curselection()
    for index in reversed(selection):
        file = global_state.batch_queue[index]
        global_state.batch_queue.pop(index)
        if file in global_state.batch_status:
            del global_state.batch_status[file]
    update_batch_list()

def clear_queue():
    global_state.batch_queue.clear()
    global_state.batch_status.clear()
    update_batch_list()

def update_batch_list():
    """Update the batch listbox with simple status."""
    batch_listbox.delete(0, tk.END)
    for file in global_state.batch_queue:
        status = global_state.batch_status.get(file, "Unknown")
        filename = os.path.basename(file)
        batch_listbox.insert(tk.END, f"{filename} [{status}]")

def stop_batch():
    global_state.should_stop = True
    batch_stop_button.configure(state="disabled")
    batch_start_button.configure(state="normal")
    messagebox.showinfo("Processing", "Stopping after current file completes...")

def start_batch():
    if not global_state.batch_queue:
        messagebox.showwarning("Warning", "No files in queue")
        return
    
    if not global_state.batch_processing:
        global_state.should_stop = False
        global_state.batch_processing = True
        batch_start_button.configure(state="disabled")
        batch_stop_button.configure(state="normal")
        process_next_in_queue()

def process_next_in_queue():
    if global_state.should_stop or not global_state.batch_processing or not global_state.batch_queue:
        global_state.batch_processing = False
        global_state.should_stop = False
        batch_start_button.configure(state="normal")
        batch_stop_button.configure(state="disabled")
        # Reset progress bars
        yolo_progress_bar["value"] = 0
        tracking_progress_bar["value"] = 0
        yolo_progress_percent.config(text="0%")
        tracking_progress_percent.config(text="0%")
        batch_count_label.config(text="0/0 files processed")
        if global_state.should_stop:
            messagebox.showinfo("Complete", "Batch processing stopped")
        else:
            messagebox.showinfo("Complete", "Batch processing complete")
        return

    global_state.current_batch_index += 1
    if global_state.current_batch_index >= len(global_state.batch_queue):
        global_state.current_batch_index = -1
        global_state.batch_processing = False
        batch_start_button.configure(state="normal")
        batch_stop_button.configure(state="disabled")
        # Reset progress bars
        yolo_progress_bar["value"] = 0
        tracking_progress_bar["value"] = 0
        yolo_progress_percent.config(text="0%")
        tracking_progress_percent.config(text="0%")
        batch_count_label.config(text="0/0 files processed")
        messagebox.showinfo("Complete", "Batch processing complete")
        return

    current_file = global_state.batch_queue[global_state.current_batch_index]
    global_state.batch_status[current_file] = "Starting..."
    update_batch_list()
    
    # Update batch progress count
    current_count = global_state.current_batch_index + 1
    total_count = len(global_state.batch_queue)
    batch_count_label.config(text=f"{current_count}/{total_count} files processed")
    
    # Set the current file as the video path
    video_path.set(current_file)
    
    # Start processing this file
    start_processing()

# Add buttons to the button frame
ttk.Button(batch_button_frame, text="Add Files", command=add_files).grid(row=0, column=0, padx=5, pady=5)
ttk.Button(batch_button_frame, text="Remove Selected", command=remove_selected).grid(row=0, column=1, padx=5, pady=5)
ttk.Button(batch_button_frame, text="Clear Queue", command=clear_queue).grid(row=0, column=2, padx=5, pady=5)
batch_start_button = ttk.Button(batch_button_frame, text="Start Batch", command=start_batch)
batch_start_button.grid(row=0, column=3, padx=5, pady=5)
batch_stop_button = ttk.Button(batch_button_frame, text="Stop Batch", command=stop_batch, state="disabled")
batch_stop_button.grid(row=0, column=4, padx=5, pady=5)

# Create and configure the listbox with scrollbar
batch_list_frame = ttk.Frame(batch_frame)
batch_list_frame.grid(row=1, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")

batch_listbox = tk.Listbox(batch_list_frame, height=6, selectmode=tk.EXTENDED, font=("Courier", 10))
batch_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

scrollbar = ttk.Scrollbar(batch_list_frame, orient=tk.VERTICAL, command=batch_listbox.yview)
scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

batch_listbox.configure(yscrollcommand=scrollbar.set)

# Enable drag and drop
def drop(event):
    """Handle drag and drop of files, properly handling paths with spaces and special characters across platforms."""
    # Get the raw data from the drop event
    raw_data = event.data
    current_mode = "VR" if global_state.isVR else "2D"
    
    # Process the raw data into a list of paths
    if platform.system() == 'Windows':
        # Windows: Handle paths with curly braces
        if raw_data.startswith('{'):
            # Split on '} {' to handle multiple files
            paths = [p.strip('{}') for p in raw_data.split('} {')]
        else:
            paths = raw_data.split()
    else:
        # Unix/Mac: Paths are typically space-separated and may be quoted
        paths = []
        for path in raw_data.split():
            # Remove any quotes
            path = path.strip('"\'')
            paths.append(path)

    # Process each file
    for file in paths:
        try:
            # Convert to absolute path and normalize
            file = os.path.normpath(os.path.abspath(file))
            
            # Check if it's a video file
            if file.lower().endswith(('.mp4', '.avi', '.mov', '.mkv')):
                if file not in global_state.batch_queue:
                    # Check if file exists and is readable
                    try:
                        with open(file, 'rb') as f:
                            pass
                        if validate_video_mode(file):
                            global_state.batch_queue.append(file)
                            global_state.batch_status[file] = "Queued"
                            current_file_status.config(text=f"Added: {os.path.basename(file)}")
                        else:
                            current_file_status.config(text=f"Skipped {os.path.basename(file)} - Wrong video type for {current_mode} mode")
                    except IOError as e:
                        global_state.logger.error(f"Could not access file: {file} - {str(e)}")
                        current_file_status.config(text=f"Error: Could not access {os.path.basename(file)}")
        except Exception as e:
            global_state.logger.error(f"Error processing dropped file: {str(e)}")
            current_file_status.config(text=f"Error processing dropped file")
            
    update_batch_list()

# Configure the listbox for drag and drop
batch_listbox.drop_target_register(DND_FILES)
batch_listbox.dnd_bind('<<Drop>>', drop)

# Quit Button
button_frame = ttk.Frame(root)
button_frame.grid(row=6, column=0, columnspan=3, padx=5, pady=10)

ttk.Button(button_frame, text="Quit", command=quit_application).grid(row=0, column=2, padx=5, pady=5)

# Footer
footer_label = ttk.Label(root, text="Individual and personal use only.\nNot for commercial use.\nk00gar 2025 - https://github.com/ack00gar", font=("Arial", 10, "italic", "bold"), justify="center")
footer_label.grid(row=7, column=0, columnspan=3, padx=5, pady=5)

def format_time(seconds):
    """Convert seconds to human readable time format."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        seconds = int(seconds % 60)
        return f"{minutes}m {seconds}s"
    else:
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        return f"{hours}h {minutes}m"

def update_progress(progress, stage):
    """Update the progress bars and status labels based on the current processing stage."""
    # Calculate time estimation
    time_str = ""
    if global_state.processing_start_time is not None and progress > 0:
        elapsed_time = time.time() - global_state.processing_start_time
        estimated_total = elapsed_time / (progress / 100)
        remaining_time = estimated_total - elapsed_time
        time_str = f"ETA: {format_time(remaining_time)}"

    if stage == "yolo":
        yolo_progress_bar["value"] = progress
        yolo_progress_percent.config(text=f"{progress:.0f}% - {time_str}")
        tracking_progress_bar["value"] = 0
        tracking_progress_percent.config(text="0%")
    elif stage == "tracking":
        tracking_progress_bar["value"] = progress
        tracking_progress_percent.config(text=f"{progress:.0f}% - {time_str}")
    
    if global_state.batch_processing:
        current_file = global_state.batch_queue[global_state.current_batch_index]
        current_count = global_state.current_batch_index + 1
        total_count = len(global_state.batch_queue)
        batch_count_label.config(text=f"{current_count}/{total_count} files processed")
        
        # Update current file status
        filename = os.path.basename(current_file)
        stage_text = "YOLO Detection" if stage == "yolo" else "Tracking Analysis"
        current_file_status.config(text=f"{filename}\n{stage_text} ({progress:.0f}%)")
        
        # Update simple status in list
        global_state.batch_status[current_file] = "Processing"
        update_batch_list()
    
    root.update_idletasks()

root.mainloop()
