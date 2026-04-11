import os
import json
import gspread
from flask import Flask, render_template, request, jsonify
from google.oauth2.service_account import Credentials
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# --- CONFIGURATION ---
SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME")
TAB_NAME = os.environ.get("GOOGLE_TAB_NAME")

def get_google_sheet():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file("google_creds.json", scopes=scope)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).worksheet(TAB_NAME)

# --- ROUTES ---
@app.route('/')
def index():
    try:
        with open('mls_data.json', 'r') as f:
            data = json.load(f)
            mls_list = data.get('mls_names', [])
    except Exception as e:
        mls_list = []
        print(f"Error loading JSON: {e}")
    return render_template('index.html', mls_list=sorted(mls_list))

@app.route('/get_recent')
def get_recent():
    try:
        sheet = get_google_sheet()
        # Gets all rows, skips the header row [1:]
        records = sheet.get_all_values()[1:]
        # Returns the bottom 5 rows to the frontend table
        return jsonify(records[-5:])
    except Exception as e:
        print(f"Error reading sheet: {e}")
        return jsonify([])

@app.route('/submit', methods=['POST'])
def submit():
    try:
        data = request.json
        hid = data.get('hid')
        
        sheet = get_google_sheet()

        # 1. Duplicate Check
        existing_hids = sheet.col_values(1)[1:] 
        if hid in existing_hids:
            return jsonify({"status": "error", "message": f"Duplicate Alert: HID {hid} is already in the sheet!"}), 400

        # 2. Time Math
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        time_taken_string = ""

        if start_time and end_time:
            try:
                start_dt = datetime.strptime(start_time, "%H:%M:%S")
                end_dt = datetime.strptime(end_time, "%H:%M:%S")
                duration = end_dt - start_dt
                total_seconds = int(duration.total_seconds())
                if total_seconds < 0: total_seconds += 86400 
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                time_taken_string = f"{hours} H {minutes} M"

            except:
                time_taken_string = "Invalid Time"

        #  9-column row
        new_row = [
            hid,
            data.get('user_name', ''),     
            data.get('mls_name', ''),
            data.get('prop_type'),      
            data.get('home_type', ''),     
            data.get('listing_date', ''),  
            data.get('status', ''),        
            time_taken_string,         
            datetime.now().strftime("%Y-%m-%d %H:%M:%S") 
        ]

#saves the sheet
        sheet.append_row(new_row)
        return jsonify({"status": "success", "message": f"Saved {hid}!"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/update', methods=['POST'])
def update_entry():
    try:
        data = request.json
        hid = data.get('hid')
        sheet = get_google_sheet()

        hids = sheet.col_values(1)
        try:
            row_index = hids.index(hid) + 1 
        except ValueError:
            return jsonify({"status": "error", "message": "HID not found!"}), 404

        existing_row = sheet.row_values(row_index)
        while len(existing_row) < 9:
            existing_row.append("")

        existing_row[1] = data.get('user_name')
        existing_row[2] = data.get('mls_name')
        existing_row[3] = data.get('prop_type')
        existing_row[4] = data.get('home_type')
        existing_row[5] = data.get('listing_date')
        existing_row[6] = data.get('status')
        
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        if start_time and end_time:
            try:    
                start_dt = datetime.strptime(start_time, "%H:%M:%S")
                end_dt = datetime.strptime(end_time, "%H:%M:%S")
                duration = end_dt - start_dt
                total_seconds = int(duration.total_seconds())
                if total_seconds < 0: total_seconds += 86400 
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                existing_row[7] = f"{hours} H {minutes} M"
            except ValueError:
                pass #this keeps the old time if its invalid

        sheet.update(f'A{row_index}:H{row_index}', [existing_row[:9]])
        return jsonify({"status": "success", "message": f"Updated {hid}!"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == '__main__':
    app.run(debug=True)