import os
import cv2
import csv
import time
import face_recognition
import RPi.GPIO as GPIO
from gpiozero import OutputDevice
from datetime import datetime
from cryptography.fernet import Fernet
from pyfingerprint.pyfingerprint import PyFingerprint
from picamera2 import Picamera2
import requests
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import dlib
import numpy as np
from scipy.spatial import distance
import board
import busio
import adafruit_mlx90614

FAILURE_FILE = "failure_count.txt"

def load_failure_count():
    try:
        with open(FAILURE_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return 0

def save_failure_count(count):
    with open(FAILURE_FILE, "w") as f:
        f.write(str(count))

# ===Configuration ===
SERVER_URL = "http://192.168.137.63:5000/log_attempt"
SENDER_EMAIL = "divudaroch67@gmail.com"
SENDER_PASSWORD = "xpmq bcxa kkaj vthx"
RECEIVER_EMAIL = "divudaroch67@gmail.com"
failure_count = load_failure_count()

# === Email ALert ===
def send_email_alert(method):
    print("[Email Debug] Preparing to send alert")
    
    subject = "Unauthorized Access Detected"
    body = f"""
    Alert!!! Multiple failed attempts
    Last Failed Method: {method}
    Time {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

    Please investigate immediately.
    """
    
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        print("[EMAIL DEBUG] Alert email sent successfully.")
    except Exception as e:
        print("[EMAIL ERROR]", e)
    
def log_attempt(method, result, temp="--", image=""):
    payload = {
        "timestamp": time.ctime(),
        "method": method,
        "result": result,
        "temp": temp,
        "image": image
    }

    try:
        response = requests.post(SERVER_URL, json=payload)
        print("[Cloud Log]", response.status_code, response.json())
    except Exception as e:
        print("[Server Log Error]", e)

# === Cloud Logging ===
def process_attempt(method, result, temp="--", image=""):
    global failure_count
    
    result = result.strip().lower()
    print(f"[DEBUG CLEANED] result={result}")  # <-- Show cleaned version

    log_attempt(method, result, temp)
    
    print(f"[DEBUG] process_attempt called with: method={method}, result={result}")


    if result == "fail":
        failure_count += 1
        save_failure_count(failure_count)
        print(f"[FAILURE COUNT] {failure_count} (last: {method})")
        
        if failure_count >= 3:
            print("[Email Debug] Preparing to send alert")
            send_email_alert(method)
            failure_count = 0
            save_failure_count(0)  # reset after email
            print("Reset after alert.")
    else:
        failure_count = 0
        save_failure_count(0)  # reset on success
        print("Reset on success")

# === GPIO/Relay Setup ===
RELAY_PIN = 18
relay = OutputDevice(RELAY_PIN, active_high=True)
relay.off()

# === MLX90614 Setup ===
i2c = busio.I2C(board.SCL, board.SDA)
mlx = adafruit_mlx90614.MLX90614(i2c)


# === Face Recognition Setup ===
trusted_encodings, trusted_names = [], []
for file in os.listdir("trusted_faces"):
    if file.endswith(('.jpg', '.png', '.jpeg')):
        img = face_recognition.load_image_file(os.path.join("trusted_faces", file))
        enc = face_recognition.face_encodings(img)
        if enc:
            trusted_encodings.append(enc[0])
            trusted_names.append(os.path.splitext(file)[0])

picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(main={"format": "RGB888"}))
picam2.start()
time.sleep(2)

EAR_THRESHOLD = 0.21
EAR_CONSEC_FRAMES = 2
PREDICTOR_PATH = "/home/daroch/shape_predictor_68_face_landmarks.dat"

GPIO.setmode(GPIO.BCM)
IR_LED_PIN = 16

GPIO.setup(IR_LED_PIN, GPIO.OUT)
GPIO.output(IR_LED_PIN, GPIO.LOW)

# Eye landmark indices
(lStart, lEnd) = (42, 48)  # Left eye
(rStart, rEnd) = (36, 42)  # Right eye

detector = dlib.get_frontal_face_detector()
predictor = dlib.shape_predictor(PREDICTOR_PATH)

def eye_aspect_ratio(eye):
    A = distance.euclidean(eye[1], eye[5])
    B = distance.euclidean(eye[2], eye[4])
    C = distance.euclidean(eye[0], eye[3])
    return (A + B) / (2.0 * C)

# === Load accessible users ===
def load_accessible_users(path="accessible_users.txt"):
    try:
        with open(path, "r") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print("[WARNING] accessible_users.txt not found.")
        return []

accessible_users = load_accessible_users()

# === Modified Face Auth ===
def face_auth():
    print("[Step 1] Show your face and blink...")

    # Temperature check
    try:
        temp = mlx.object_temperature
        print(f"[TEMP] {temp:.2f} C")
        if temp < 25.0:
            print("Possible spoof (cold face)")
            process_attempt("face", "fail", f"{temp:.2f}")
            return False, None
    except Exception as e:
        print("[TEMP SENSOR ERROR]", e)
        process_attempt("face", "fail", "sensor error")
        return False, None

    timeout = time.time() + 10
    blink_counter = 0
    detected_name = "Unknown"

    GPIO.output(IR_LED_PIN, GPIO.HIGH)  # Turn on IR LED

    while time.time() < timeout:
        frame = picam2.capture_array()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

        face_locations = face_recognition.face_locations(rgb)
        face_encodings = face_recognition.face_encodings(rgb, face_locations)
        rects = detector(gray, 0)

        for face_encoding, (top, right, bottom, left) in zip(face_encodings, face_locations):
            matches = face_recognition.compare_faces(trusted_encodings, face_encoding)
            if True in matches:
                idx = matches.index(True)
                detected_name = trusted_names[idx]
                cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)
                cv2.putText(frame, detected_name, (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        for rect in rects:
            shape = predictor(gray, rect)
            shape_np = np.array([[p.x, p.y] for p in shape.parts()])
            leftEye = shape_np[lStart:lEnd]
            rightEye = shape_np[rStart:rEnd]
            ear = (eye_aspect_ratio(leftEye) + eye_aspect_ratio(rightEye)) / 2.0

            if ear < EAR_THRESHOLD:
                blink_counter += 1
            else:
                if blink_counter >= EAR_CONSEC_FRAMES:
                    print(f"[BLINK OK] {detected_name}")
                    GPIO.output(IR_LED_PIN, GPIO.LOW)
                    process_attempt("face", "success", f"{temp:.2f}")
                    #cv2.destroyAllWindows()
                    return True, detected_name
                blink_counter = 0

        cv2.imshow("Face + Blink", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

        time.sleep(0.1)

    GPIO.output(IR_LED_PIN, GPIO.LOW)
    print("[FACE FAIL] No valid blink")
    process_attempt("face", "fail")
    cv2.destroyAllWindows()
    return False, None



# === Fingerprint Setup ===
finger_names = {}
try:
    with open("finger_names.csv", "r") as file:
        reader = csv.DictReader(file)
        for row in reader:
            finger_names[int(row["Position"])] = row["Name"]
except:
    pass

try:
    sensor = PyFingerprint('/dev/serial0', 57600)
    if not sensor.verifyPassword():
        raise ValueError("Wrong fingerprint sensor password")
except Exception as e:
    print("Fingerprint error:", e)
    exit(1)

def fingerprint_auth():
    print("[Step 2] Place your finger...")
    timeout = time.time() + 10
    while time.time() < timeout:
        if sensor.readImage():
            sensor.convertImage(0x01)
            position, _ = sensor.searchTemplate()
            if position >= 0:
                name = finger_names.get(position, f"USER_{position}")
                print(f"Fingerprint matched: {name}")
                return True
            else:
                print("Fingerprint not recognized.")
                process_attempt("fingerprint", "fail")
                
                return False
        time.sleep(0.2)

    print("No finger detected.")
    process_attempt("fingerprint", "fail")
    return False

# === Keypad Setup ===
KEYPAD = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["*", "0", "#"]]
ROW_PINS = [17, 27, 22, 23]
COL_PINS = [24, 4, 25]
GPIO.setmode(GPIO.BCM)

for row in ROW_PINS:
    GPIO.setup(row, GPIO.IN, pull_up_down=GPIO.PUD_UP)
for col in COL_PINS:
    GPIO.setup(col, GPIO.OUT)
    GPIO.output(col, GPIO.HIGH)

def get_key():
    for j, col in enumerate(COL_PINS):
        GPIO.output(col, GPIO.LOW)
        for i, row in enumerate(ROW_PINS):
            if GPIO.input(row) == GPIO.LOW:
                while GPIO.input(row) == GPIO.LOW:
                    time.sleep(0.01)
                GPIO.output(col, GPIO.HIGH)
                return KEYPAD[i][j]
        GPIO.output(col, GPIO.HIGH)
    return None

     
    
# === Decrypt stored password ===
with open("/home/daroch/.secure/secret.key", "rb") as kf:
    key = kf.read()
cipher = Fernet(key)
with open("/home/daroch/.secure/password.txt", "rb") as pf:
    encrypted = pf.read()
SECRET_PASSWORD = cipher.decrypt(encrypted).decode()

def keypad_auth():
    print("[Step 3] Enter keypad password (3 attempts allowed):")
    attempts_left = 3
    while attempts_left > 0:
        buf = ""
        timeout = time.time() + 20
        print(f"\nAttempts left: {attempts_left}")
        print("Enter password:")

        while time.time() < timeout:
            key = get_key()
            if key:
                print("*", end="", flush=True)
                buf += key
                if len(buf) == len(SECRET_PASSWORD):
                    break
            time.sleep(0.1)

        if buf == SECRET_PASSWORD:
            print("\nKeypad OK")
            process_attempt("keypad", "success")
            return True
        else:
            print("\nWrong password")
            attempts_left -= 1
            if attempts_left > 0:
                print("Try again...")
    
    # If all attempts used
    print("All keypad attempts failed.")
    process_attempt("keypad", "fail")
    return False


# === Main Flow ===
try:
    print("=== TRIPLE AUTHENTICATION SYSTEM ===")
    success, username = face_auth()

    if success:
        if username in accessible_users:
            print(f"[ACCESSIBLE USER] {username} authenticated via face only.")
            relay.on()
            time.sleep(5)
            relay.off()
        else:
            if fingerprint_auth():
                if keypad_auth():
                    print("Access Fully Granted! Door Unlocked.")
                    relay.on()
                    time.sleep(5)
                    relay.off()
                else:
                    print("Access Denied at Keypad.")
            else:
                print("Access Denied at Fingerprint.")
    else:
        print("Access Denied at Face.")

except KeyboardInterrupt:
    print("Exiting...")

finally:
    picam2.stop()
    GPIO.cleanup()
    relay.off()
    print("System stopped. Door locked.")
