from machine import Pin, I2C, ADC
import dht
import time
import utime # Kept for microsecond delays in dust sensor
import ssd1306
import math
import network
import urequests
import json

# =========================================================
# --- WIFI & EMAIL CONFIGURATION (UPDATE THESE) ---
# =========================================================

WIFI_SSID = "sanju"
WIFI_PASSWORD = "nothings"

# Using Formspree.io for email (FREE & EASY)
FORMSPREE_URL = "https://formspree.io/f/mwprybnb"  

SENDER_EMAIL = "mvaradarajulu25@gmail.com"
RECIPIENT_EMAIL = "kamaleshpayani88@gmail.com"  # SAME EMAIL - sends to yourself

# Email sending interval (in seconds)
# Removed for single-send requirement, logic is now event-driven (initial/alert).
# EMAIL_SEND_INTERVAL = 30  

# =========================================================
# --- HARDWARE & CALIBRATION CONFIGURATION ---
# =========================================================

R0_CALIBRATION_COMPLETE = True # Set to True once R0_CLEAN_AIR is finalized

# --- Pin Assignments ---
I2C_SDA_PIN = 21
I2C_SCL_PIN = 22
DHT_PIN = 4
MQ135_PIN = 36         # ADC pin for MQ-135 (Gas)
DUST_LED_PIN = 2       # GPIO pin for GP2Y1010AU0F LED control (via 150-ohm resistor)
DUST_VOUT_PIN = 34     # ADC pin for GP2Y1010AU0F Analog Output (Yellow wire)

# --- MQ-135 Calibration Constants ---
R0_CLEAN_AIR = 10.0    # Calibrated Rs/R0 value in clean air (in kOhms)
R_LOAD = 10.0          # Load Resistor value (in kOhms)
A = 110.0              # Formula constant (PPM = A * (Rs/R0)^B)
B = -2.65              # Formula constant

# --- General ADC/OLED Constants ---
OLED_WIDTH = 128
OLED_HEIGHT = 64
I2C_ADDR = 0x3c 
VOLTAGE_RESOLUTION = 3.3
ADC_MAX = 4095

# --- GP2Y1010AU0F Timing Constants (CRITICAL) ---
SAMPLING_TIME = 280     # Time (us) to wait after LED ON before reading
PULSE_WIDTH = 320       # Total time LED is ON (us)
SLEEP_TIME = 9680       # Time (us) to wait for the rest of the 10ms cycle
DUSTY_THRESHOLD = 0.15  # Dust/Particulate density (mg/m^3)
SMOKE_THRESHOLD = 0.50  # Smoke/Heavy pollution threshold (mg/m^3)

# =========================================================
# --- INITIALIZATION ---
# =========================================================

# DHT Sensor
dht_sensor = dht.DHT11(Pin(DHT_PIN))

# MQ-135 ADC
mq135_adc = ADC(Pin(MQ135_PIN))
mq135_adc.atten(ADC.ATTN_11DB) 
mq135_adc.width(ADC.WIDTH_12BIT) 

# GP2Y1010AU0F (Dust/Smoke) Setup
led_power = Pin(DUST_LED_PIN, Pin.OUT)
led_power.value(1) # Start HIGH (LED OFF - it's active-low)

dust_adc = ADC(Pin(DUST_VOUT_PIN))
dust_adc.atten(ADC.ATTN_11DB) # Set full range 0-3.3V

# OLED Display
try:
    # --- FIX: Ensure SCL and SDA pins are separate ---
    i2c = I2C(0, scl=Pin(I2C_SCL_PIN), sda=Pin(I2C_SDA_PIN)) 
    oled = ssd1306.SSD1306_I2C(OLED_WIDTH, OLED_HEIGHT, i2c, addr=I2C_ADDR)
except Exception as e:
    print(f"I2C or OLED init failed: {e}")
    time.sleep(5)

# WiFi
wlan = network.WLAN(network.STA_IF)

# =========================================================
# --- WIFI CONNECTION FUNCTION ---
# =========================================================

def connect_wifi():
    """Connect to WiFi network."""
    try:
        wlan.active(True)
        time.sleep(1)
        
        if not wlan.isconnected():
            print(f"Connecting to WiFi: {WIFI_SSID}")
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)
            
            timeout = 20
            while not wlan.isconnected() and timeout > 0:
                print(".", end="")
                time.sleep(1)
                timeout -= 1
            
            if wlan.isconnected():
                print("\nWiFi Connected!")
                print(f"IP: {wlan.ifconfig()[0]}")
                return True
            else:
                print("\nFailed to connect to WiFi!")
                return False
        else:
            print("Already connected to WiFi")
            return True
    except Exception as e:
        print(f"WiFi Error: {e}")
        return False

# =========================================================
# --- EMAIL SENDING FUNCTION ---
# =========================================================

def send_email(temp, hum, ppm, gas_status, dust_density, dust_status):
    """Send sensor data via email using Formspree (RECOMMENDED)."""
    try:
        print("Sending email via web service...")
        
        # Prepare email data
        email_data = {
            "email": SENDER_EMAIL,
            "message": f"""Air Quality Report

--- Environmental Data ---
Temperature: {temp:.1f} C
Humidity: {hum:.1f} %

--- Gas Pollution (MQ-135) ---
CO2 Equivalent: {int(ppm)} PPM
Gas Quality Status: {gas_status}

--- Particulate Pollution (GP2Y1010AU0F) ---
Particulate Density: {dust_density:.3f} mg/m^3
Dust/Smoke Status: {dust_status}

Device: ESP32 Air Monitor
Time: {time.time()}
"""
        }
        
        # Send via HTTP POST
        response = urequests.post(FORMSPREE_URL, json=email_data)
        
        if response.status_code == 200:
            print("✅ Email sent successfully!")
            response.close()
            return True
        else:
            print(f"Email failed: {response.status_code} - Reason: {response.text}")
            response.close()
            return False
            
    except Exception as e:
        print(f"Email error: {e}")
        return False

# =========================================================
# --- SENSOR PROCESSING FUNCTIONS ---
# =========================================================

def read_dust_sensor():
    """
    Performs the precise, timed measurement cycle for the GP2Y1010AU0F.
    This function must be called every 10ms to maintain accuracy.
    """
    # 1. Turn IR LED ON (Active LOW)
    led_power.value(0)
    
    # 2. Wait for reading to stabilize (280us)
    utime.sleep_us(SAMPLING_TIME)
    
    # 3. Read the analog voltage
    adc_value = dust_adc.read()
    
    # 4. Turn IR LED OFF (Active HIGH)
    led_power.value(1)
    
    # 5. Wait for the rest of the 10ms cycle
    utime.sleep_us(SLEEP_TIME)

    # Convert the raw ADC value to voltage
    voltage = adc_value * (VOLTAGE_RESOLUTION / ADC_MAX)

    # --- Convert to Dust Density (GP2Y1010AU0F Formula) ---
    # Density (mg/m^3) = 0.172 * Vout - 0.0999
    dust_density = (0.172 * voltage) - 0.0999
    
    # Ensure density is not negative (if it is, it means clean air, so set to 0)
    if dust_density < 0:
        dust_density = 0
        
    # Return the raw ADC value as well for debugging
    return adc_value, voltage, dust_density

def classify_dust_quality(density):
    """
    Classifies the air based on dust/particulate density.
    """
    if density >= SMOKE_THRESHOLD:
        return "SMOKE/CRITICAL"
    elif density >= DUSTY_THRESHOLD:
        return "DUSTY/MODERATE"
    else:
        return "CLEAN"

def calculate_Rs(raw_adc):
    """Calculates Sensor Resistance (Rs) in kOhms for MQ-135."""
    voltage = raw_adc * (VOLTAGE_RESOLUTION / ADC_MAX)
    
    # Use R_LOAD in kOhm for calculation consistency
    if voltage > 0:
        Rs = R_LOAD * ((VOLTAGE_RESOLUTION / voltage) - 1)
        return Rs
    return 0.0

def get_mq135_ppm(Rs):
    """Converts Sensor Resistance (Rs) to CO2 equivalent PPM."""
    
    if R0_CLEAN_AIR <= 0 or not R0_CALIBRATION_COMPLETE:
        return 0.0

    Rs_R0 = Rs / R0_CLEAN_AIR
    # The math.pow function is used correctly here: A * (Rs/R0)^B
    ppm = A * math.pow(Rs_R0, B) 
    
    return ppm

def classify_gas_quality(ppm):
    """Classifies air quality based on CO2 equivalent PPM standards for MQ-135."""
    if ppm <= 800:
        return "GOOD"
    elif ppm <= 1500:
        return "MODERATE"
    else:
        return "BAD"

# =========================================================
# --- DISPLAY FUNCTIONS ---
# =========================================================

def display_readings(temp, hum, ppm, gas_status, dust_density, dust_status, wifi_status):
    """Normal operational display."""
    oled.fill(0) 
    oled.text("T/H:", 0, 0)
    oled.text(f"{temp:.1f}C/{hum:.1f}%", 30, 0)
    
    # Gas Pollution
    oled.text("GAS (MQ135):", 0, 15)
    oled.text(f"{int(ppm)} PPM", 0, 30)
    oled.text(f"({gas_status})", 70, 30)

    # Particulate Pollution
    oled.text("DUST/SMOKE:", 0, 45)
    oled.text(f"{dust_density:.2f} mg/m^3", 0, 55)
    oled.text(f"({dust_status})", 70, 55)
    
    # WiFi Status on bottom right
    oled.text(f"W:{wifi_status}", 100, 0)

    oled.show()

def display_wifi_connecting():
    """Display WiFi connection status."""
    oled.fill(0)
    oled.text("Connecting WiFi...", 0, 25)
    oled.text(WIFI_SSID[:16], 0, 40)
    oled.show()

def display_sending_email():
    """Display email sending status."""
    oled.fill(0)
    oled.text("Sending Email...", 0, 25)
    oled.show()

# =========================================================
# --- MAIN LOOP ---
# =========================================================

if R0_CALIBRATION_COMPLETE:
    print(f"Starting Integrated Air Monitor with WiFi & Email.")
    
    # Connect to WiFi
    display_wifi_connecting()
    time.sleep(2)
    wifi_connected = connect_wifi()
    
    # Use a flag to ensure the startup email is only sent once
    initial_email_sent = False
    
    # Use a flag to ensure the bad air quality alert is only sent once per bad event
    alert_email_sent = False 
    
    while True:
        try:
            temp = 0.0
            hum = 0.0
            
            # 1. Read DHT11 Sensor
            try:
                dht_sensor.measure()
                temp = dht_sensor.temperature()
                hum = dht_sensor.humidity()
            except OSError:
                print("DHT11 error, skipping measurement.")
                pass 

            # 2. Read GP2Y1010AU0F (Dust/Smoke) Sensor
            dust_raw, dust_voltage, dust_density = read_dust_sensor()
            dust_status = classify_dust_quality(dust_density)

            # 3. Read MQ-135 (Gas) Sensor, Calculate PPM, and Classify
            mq_raw = mq135_adc.read()
            Rs = calculate_Rs(mq_raw)
            ppm = get_mq135_ppm(Rs)
            gas_status = classify_gas_quality(ppm)
            
            # Determine WiFi status display
            wifi_status = "ON" if wlan.isconnected() else "OFF"
            
            # 4. Display on OLED
            display_readings(temp, hum, ppm, gas_status, dust_density, dust_status, wifi_status)

            # 5. Print to Console
            print("-" * 40)
            print(f"ENV: T={temp:.1f}C, H={hum:.1f}%, WiFi={wifi_status}")
            print(f"GAS (MQ-135): {int(ppm)} PPM ({gas_status}) | Rs={Rs:.2f} kOhm | Raw ADC={mq_raw}")
            print(f"DUST (GP2Y): {dust_density:.3f} mg/m^3 ({dust_status}) | Vout={dust_voltage:.3f} V | Raw ADC={dust_raw}")
            
            # --- EMAIL LOGIC ---
            
            is_bad_air = (gas_status == "BAD" or dust_status == "SMOKE/CRITICAL")

            # A. Send Initial Startup Email (Once)
            if not initial_email_sent and wlan.isconnected():
                display_sending_email()
                print("Sending initial startup email...")
                if send_email(temp, hum, ppm, gas_status, dust_density, dust_status):
                    print("Initial startup email sent successfully!")
                    initial_email_sent = True # Set flag to prevent future startup emails
                else:
                    print("Failed to send initial startup email")
                time.sleep(2) 
            
            # B. Send Alert Email if air quality is BAD (Once per event)
            if is_bad_air and not alert_email_sent and wlan.isconnected():
                display_sending_email()
                print("Sending BAD air quality alert email...")
                
                # Send the full report as the alert
                if send_email(temp, hum, ppm, gas_status, dust_density, dust_status):
                    print("Alert email sent successfully!")
                    alert_email_sent = True # Set flag to prevent spamming during the bad period
                else:
                    print("Failed to send alert email")
                time.sleep(2)
            
            # C. Reset alert flag if air quality improves
            elif not is_bad_air and alert_email_sent:
                alert_email_sent = False
                print("Air quality improved, alert flag reset.")

            time.sleep(5)
            
        except Exception as e:
            print(f"Critical Error in Main Loop: {e}")
            time.sleep(5)
