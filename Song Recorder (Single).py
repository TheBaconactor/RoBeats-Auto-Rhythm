import time
import re
import mss
import mss.tools
from datetime import datetime, timedelta
import keyboard
import pytesseract
from PIL import ImageGrab
from concurrent.futures import ThreadPoolExecutor
import math

# Set the path to tesseract.exe
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def sanitize_filename(filename):
    return re.sub(r'[\\/*?:"|<>.]', '', filename)

def ocr_scan_region(window_name, region):
    screenshot = ImageGrab.grab(bbox=region)
    text = pytesseract.image_to_string(screenshot)
    return text

def get_timestamp(start_time):
    return "{:.4f}".format((datetime.now() - start_time).total_seconds())

def color_in_range(color, target_range):
    return target_range[0] <= color <= target_range[1]

def wait_for_timer_start():
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        timer_region_start = (540, 1284)  # Converted coordinates
        timer_color_range = (20000, 22000)  # RGB integer value
        region_size = 3  # 3x3 pixel area
        while True:
            if keyboard.is_pressed('q'):
                print("Exiting script...")
                exit(0)
            found = False
            for x in range(timer_region_start[0], timer_region_start[0] + region_size):
                for y in range(timer_region_start[1], timer_region_start[1] + region_size):
                    img = sct.grab({'top': y, 'left': x, 'width': 1, 'height': 1})
                    color = img.pixel(0, 0)
                    if color_in_range(sum(color[:3]), timer_color_range):  # Sum RGB values
                        found = True
                        break
                if found:
                    break
            if found:
                print("Timer activated!")
                return True
            time.sleep(0.001)

# Define primary and secondary color dictionaries
pixel1_colors = {
    "Chill": 12946040,
    "Rush": 5468915,
    "Vibe": 7980671,
    "Flow": 11959997,
    "Beat": 6268921
}

pixel2_colors = {
    "Chill": [12946040, 11692353],
    "Rush": [5468915, 2367725],
    "Vibe": [7980671, 4896056],
    "Flow": [11959997, 10308776],
    "Beat": [6268921, 2197238]
}

def get_color_name(color_value, color_dict):
    for name, value in color_dict.items():
        if isinstance(value, list):
            if color_value in value:
                return name
        elif color_value == value:
            return name
    return "Unknown"

def format_timestamp(timestamp):
    """Format the timestamp to ensure four decimal places."""
    return "{:.4f}".format(math.floor(float(timestamp) * 10000) / 10000)

def check_pixel(coord, pixel_label, last_colors, target_colors, pixel_transitions, timestamp):
    with mss.mss() as sct:
        img = sct.grab({'top': coord[1], 'left': coord[0], 'width': 1, 'height': 1})
        current_color = sum(img.pixel(0, 0)[:3])
        if current_color in target_colors and current_color != last_colors[pixel_label]:
            if last_colors[pixel_label] is not None:  # Ignore the first read color
                pixel_transitions.append((timestamp, pixel_label, last_colors[pixel_label], current_color))
            last_colors[pixel_label] = current_color

def check_pixel_grid(top_left, color_dict):
    with mss.mss() as sct:
        region_size = 5  # 5x5 pixel area
        x_start, y_start = top_left
        for x in range(x_start, x_start + region_size):
            for y in range(y_start, y_start + region_size):
                img = sct.grab({'top': y, 'left': x, 'width': 1, 'height': 1})
                color = sum(img.pixel(0, 0)[:3])
                color_name = get_color_name(color, color_dict)
                if color_name != "Unknown":
                    return color_name
        return "Unknown"

def main():
    pixel_coords = [(410, 1080), (495, 1080), (580, 1080), (665, 1080)]  # Converted coordinates
    pixels = {i + 1: coord for i, coord in enumerate(pixel_coords)}

    target_colors = {65535, 49344, 32896, 0}
    last_colors = {pixel: None for pixel in pixels}
    pixel_transitions = []  # Store all transitions

    start_time = datetime.now() + timedelta(seconds=2.513)  # Record the start time of the script

    # Variables for checking the target color
    target_color_coord = (548, 294)  # Converted coordinates
    target_color_value = 3057349
    last_color_check_time = 0  # Time of the last color check

    try:
        with ThreadPoolExecutor() as executor:
            while True:
                current_time = time.time()
                if keyboard.is_pressed('q'):  # Check if 'q' is pressed
                    print("Exiting script...")
                    exit(0)

                # Check for target color once every second
                if current_time - last_color_check_time >= 1:
                    with mss.mss() as sct:
                        img = sct.grab({'top': target_color_coord[1], 'left': target_color_coord[0], 'width': 1, 'height': 1})
                        color = sum(img.pixel(0, 0)[:3])
                        if color == target_color_value:
                            print("Detected target color at position within main loop!")
                            time.sleep(2)  # Wait 2 seconds before breaking the loop
                            break  # Break the main loop to restart
                    last_color_check_time = current_time

                timestamp = get_timestamp(start_time)  # Get timestamp in seconds since start

                futures = []
                for pixel_label, coord in pixels.items():
                    futures.append(executor.submit(check_pixel, coord, pixel_label, last_colors, target_colors, pixel_transitions, timestamp))

                # Wait for all threads to finish
                for future in futures:
                    future.result()
    finally:
        try:
            # OCR scan
            ocr_region = (560, 296, 832, 440)  # Converted coordinates
            ocr_text = ocr_scan_region("Roblox", ocr_region)

            # Replace "|" with "I" in the OCR output
            ocr_text = ocr_text.replace('|', 'I')

            # Replace newline characters with spaces
            single_line_ocr_text = ocr_text.replace('\n', ' ')

            # Print the single-line OCR output to the console
            print("Unfiltered OCR Output (single line):")
            print(single_line_ocr_text)

            # Parse OCR text for song name and difficulty
            song_name_start = single_line_ocr_text.find("Now playing:") + len("Now playing:")
            song_name_end = single_line_ocr_text.find("(Difficulty:")
            if song_name_start > -1 and song_name_end > -1:
                song_name = single_line_ocr_text[song_name_start:song_name_end].strip()
            else:
                song_name = ""

            difficulty_start = single_line_ocr_text.find("(Difficulty:") + len("(Difficulty:")
            difficulty_end = single_line_ocr_text.find(")", difficulty_start)
            if difficulty_start > -1 and difficulty_end > -1:
                difficulty = single_line_ocr_text[difficulty_start:difficulty_end].strip()
            else:
                difficulty = ""

        except Exception as e:
            single_line_ocr_text = f"OCR scan failed: {e}"
            song_name = ""
            difficulty = ""
        
        # Initialize counters
        type_1_and_2_count = 0
        type_3_count = 0
        final_timestamp = None

        # Prepare pixel data
        pixel_data = ""
        count = 1
        for transition in pixel_transitions:
            timestamp, pixel_label, old_color, new_color = transition
            formatted_timestamp = format_timestamp(timestamp)
            final_timestamp = formatted_timestamp
            entry_type = None
            if old_color == 65535 and new_color == 0:
                entry_type = 1
            elif old_color == 65535 and new_color == 32896:
                entry_type = 2
            elif old_color == 49344 and new_color == 0:
                entry_type = 3

            if entry_type is not None:
                if entry_type in [1, 2]:
                    type_1_and_2_count += 1
                elif entry_type == 3:
                    type_3_count += 1
                pixel_data += f"{formatted_timestamp}\t{count}\t{pixel_label}\t{entry_type}\n"
                count += 1

        # Check 3x3 pixel grids for each color
        grid1_top_left = (295, 897)  # Converted coordinates
        grid2_top_left = (306, 929)  # Converted coordinates
        color1_name = check_pixel_grid(grid1_top_left, pixel1_colors)
        color2_name = check_pixel_grid(grid2_top_left, pixel2_colors)

        # Prepare header data
        fever_fill = math.floor(type_1_and_2_count / 3 * 100) / 100 if type_1_and_2_count > 0 else 0.00
        fever_time = math.floor(float(final_timestamp) * 0.15 * 10000) / 10000 if final_timestamp else 0.0000
        
        # Sanitize the song name for use in the filename and headers
        sanitized_song_name = sanitize_filename(song_name)

        output_filename = f"{sanitized_song_name}.txt" if sanitized_song_name else "pixel_changes.txt"
        
        # Prepare header data with sanitized song name
        final_header_data = f"Song Name\t{sanitized_song_name}\n" + \
                            f"Difficulty\t{difficulty}\n" + \
                            f"Primary Color\t{color1_name}\n" + \
                            f"Secondary Color\t{color2_name}\n" + \
                            f"Last Note Time\t{final_timestamp}\n" + \
                            f"Total Notes\t{count - 1}\n" + \
                            f"Fever Fill\t{fever_fill}\n" + \
                            f"Fever Time\t{fever_time}\n" + \
                            f"Long Notes\t{type_3_count}\n\nSong Data\n"

        with open(output_filename, 'w') as file:
            file.write(final_header_data)
            file.write(pixel_data)

        print(f"Data written to: {output_filename}")

# Main loop
while True:
    # First, wait for the timer to activate
    timer_started = wait_for_timer_start()
    if timer_started:
        # Run the main function
        main()