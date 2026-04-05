import os
import json
from flask import Flask, render_template, request, jsonify
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# --- THE ENV WAY ---
# os.environ.get("KEY") looks for a setting on Render or your PC.
# The second part is a "default" just in case you forget to set it.
SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME", "My_Default_Sheet")
TAB_NAME = os.environ.get("GOOGLE_TAB_NAME", "Sheet1")

def get_google_sheet():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file("google_creds.json", scopes=scope)
    client = gspread.authorize(creds)

    return client.open(SHEET_NAME).worksheet(TAB_NAME)

@app.route('/')
def index():
    try:
        with open('mls_data.json', 'r') as f:
            data = json.load(f)
            mls_list = data.get('mls_names', [])
    except Exception as e:
        # Fallback if json file is not found
        mls_list = ["Error loading MLS List"]
        print = (f'JSON Error: {e}')
    
    return render_template('index.html', mls_list=sorted(mls_list))

@app.route('/get_recent', methods=['GET'])
def get_recent():
    try:
        sheet = get_google_sheet()
        all_rows = sheet.get_all_values()
        if len(all_rows) <= 1:
            return jsonify([])
        
        # Get last 10 entries (excluding header), then reverse for newest first
        recent = all_rows[1:][-10:]
        recent.reverse()
        return jsonify(recent)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/submit', methods=['POST'])
def submit():
    try:
        data = request.json
        hid = data.get('hid')
        sheet = get_google_sheet()

        #Duplicate HID Check
        existing_hids = sheet.col_values(1)[1:]
        if hid in existing_hids:
            # return a bad request error for duplicate error message
            return jsonify({
                "status" : "error",
                "message" : f"Duplicate Alert: HID {hid} has already been entered!"
            }), 400

        # Time Calculation Logic
        fmt = "%H:%M:%S"
        start_dt = datetime.strptime(data.get('start_time'), fmt)
        end_dt = datetime.strptime(data.get('end_time'), fmt)
        
        duration = end_dt - start_dt
        total_seconds = int(duration.total_seconds())
        if total_seconds < 0: total_seconds += 86400 

        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60

        # --- NEW FORMATTING LOGIC ---
        # This creates a string like "1 H 23 M"
        time_taken_string = f"{hours} H {minutes} M"

        new_row = [
            hid,
            data.get('user_name'),
            data.get('mls_name'),
            data.get('prop_type'),
            data.get('home_type'),
            data.get('listing_date'),   
            data.get('status'),
            time_taken_string, # Combined string here
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]

        sheet.append_row(new_row)

        return jsonify({
            "status": "success", 
            "message": f"Saved {hid}! Duration: {time_taken_string}"
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == '__main__':
    # Render provides a $PORT environment variable
    port = int(os.environ.get("PORT", 5000))
    # host='0.0.0.0' is required for cloud hosting
    app.run(host='0.0.0.0', port=port)

    # new features to add in web app 
    # add listing date input
    # add a duplicate check pointer
    # try zoho api check, in built just getting there we get a successfull json in return or not
    # fix saving and submit buttom, saving button not changing to submit after successfull entry
    # option to edit previous entry in the bottom recent entries
    # add hometype field with options (Single Family Residence, Condo, Mobile, Manufactured)
    # add user name of who is entering the data, name should remain constant on the browser throughout the data entry session
    # if time range is not entered and submitted, submit button stops functioning, user has to refresh