import os
import json
import gspread
import psycopg2
from psycopg2.extras import RealDictCursor #returns output in a dictionary format
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
from google.oauth2.service_account import Credentials
from datetime import datetime
from dotenv import load_dotenv
from werkzeug.security import check_password_hash

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "fallback-secret-for-local-testing")

def get_db_connection():
    conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
    return conn

# --- CONFIGURATION ---
SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME")
TAB_NAME = os.environ.get("GOOGLE_TAB_NAME")

def get_google_sheet():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file("google_creds.json", scopes=scope)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).worksheet(TAB_NAME)

# --- ROUTES ---
#log in authentication
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- LOGIN & LOGOUT ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        form_username = request.form.get('username')
        form_password = request.form.get('password')
        
        try:
            # 1. Open Database Connection
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # 2. Search for user
            sql_query = "SELECT * FROM users WHERE username = %s"
            cursor.execute(sql_query, (form_username,))
            user = cursor.fetchone()
            
            # Close connection
            cursor.close()
            conn.close()

            # 3. Verify Credentials with Password Hashing
            if user and check_password_hash(user['password'], form_password):
                session['user'] = user['username']
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error="Invalid User ID or Password.")
        except Exception as e:
            return render_template('login.html', error="Database connection error. Please try again.")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None) # Erases the user from the server's memory
    return redirect(url_for('login')) # Kicks them back to the login screen

# --- PROTECTED APP ROUTES ---
@app.route('/')
@login_required
def index():
    try:
        with open('mls_data.json', 'r') as f:
            data = json.load(f)
            mls_list = data.get('mls_names', [])
    except Exception as e:
        mls_list = []
        print(f"Error loading JSON: {e}")
        
    return render_template('index.html', mls_list=sorted(mls_list), current_user=session['user'])


@app.route('/get_recent')
@login_required
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
@login_required
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
        return jsonify({"status": "success", "message": f"Saved {hid}, Time Taken: {time_taken_string}!"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/update', methods=['POST'])
@login_required
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

        sheet.update(f'A{row_index}:I{row_index}', [existing_row[:9]])
        return jsonify({"status": "success", "message": f"Updated {hid}!"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == '__main__':
    app.run(debug=True)