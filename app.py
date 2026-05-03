import os
import json
import gspread
import threading
from oauth2client.service_account import ServiceAccountCredentials
import psycopg2
from psycopg2.extras import RealDictCursor #returns output in a dictionary format
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
from google.oauth2.service_account import Credentials
from datetime import datetime
from dotenv import load_dotenv
from werkzeug.security import check_password_hash

load_dotenv()

def get_gspread_client():
    # Path to your credentials file on your laptop
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name('google_creds.json', scope)
    return gspread.authorize(creds)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "fallback-secret-for-local-testing")

def get_db_connection():
    try: 
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        return conn
    except Exception as e:
        print(f"Database Connection Error {e}")
        return None


@app.route('/test_db')
def test_db():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"status": "error", "message": "Could not connect to database. Check your password in .env!"})
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM tracker_data;")
        count = cur.fetchone()[0]

        cur.close()
        conn.close()
        return jsonify({"status": "success", "message": f"Connected! The tracker_data table has {count} rows."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
            

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
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Grab the latest 50 entries instantly from PostgreSQL
        # (Assuming you want to see recent activity)
        cur.execute("SELECT * FROM tracker_data ORDER BY log_date DESC, time_log DESC LIMIT 50;")
        records = cur.fetchall()
        
        cur.close()
        conn.close()
        
        # Convert the SQL rows into a simple list of lists for your JavaScript
        formatted_records = [list(row) for row in records]
        
        return jsonify(formatted_records)
    except Exception as e:
        print(f"Database Fetch Error: {e}")
        return jsonify([])

def sync_to_sheets_task(data, current_agent, time_taken_string, log_date):
    """Runs in the background to sync data to Google Sheets."""
    try:
        # Assuming you have your existing get_google_sheet() helper function
        sheet = get_google_sheet()
        hid = data.get('hid')
        
        new_row = [
            hid,
            current_agent,
            data.get('mls_name', ''),
            data.get('prop_type'),      
            data.get('home_type', ''),     
            data.get('listing_date', ''),  
            data.get('status', ''),        
            time_taken_string,         
            log_date
        ]

        cell = sheet.find(str(hid))
        if cell:
            range_label = f"A{cell.row}:I{cell.row}"
            sheet.update(range_label, [new_row])
            print(f"Background: Updated {hid} in Sheets.")
        else:
            sheet.append_row(new_row)
            print(f"Background: Appended {hid} to Sheets.")
            
    except Exception as e:
        print(f"Background Sheet Sync Error: {e}")


@app.route('/submit', methods=['POST'])
@login_required
def submit():
    try:
        data = request.json
        hid = data.get('hid')
        current_agent = session.get('user', 'Unknown Agent')
        
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
        log_date = datetime.now().strftime("%Y-%m-%d")

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
        INSERT INTO tracker_data (hid, agent, mls_name, prop_type, home_type, listing_date, status, time_log, log_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hid) DO UPDATE SET 
                agent = EXCLUDED.agent,
                mls_name = EXCLUDED.mls_name,
                prop_type = EXCLUDED.prop_type,
                home_type = EXCLUDED.home_type,
                listing_date = EXCLUDED.listing_date,
                status = EXCLUDED.status,
                time_log = EXCLUDED.time_log,
                log_date = EXCLUDED.log_date;

        """, (hid, current_agent, data.get('mls_name', ''), data.get('prop_type'), 
              data.get('home_type', ''), data.get('listing_date', ''), data.get('status', ''), 
              time_taken_string, log_date))
        conn.commit()
        cur.close()
        conn.close()

        thread = threading.Thread(
            target=sync_to_sheets_task,
            args=(data, current_agent, time_taken_string, log_date)
        )
        thread.start()

        return jsonify({"status": "success", "message": f"Saved {hid} Instantly!"})
    except Exception as e:
        print(f"Submission Error {e}")
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/update', methods=['POST'])
@login_required
def update_entry():
    try:
        data = request.json
        hid = data.get('hid')
        current_agent = session.get('user', 'Unknown Agent')
        
        # Time Math (Same as your submit route)
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        time_taken_string = ""
        if start_time and end_time:
            start_dt = datetime.strptime(start_time, "%H:%M:%S")
            end_dt = datetime.strptime(end_time, "%H:%M:%S")
            total_seconds = int((end_dt - start_dt).total_seconds())
            if total_seconds < 0: total_seconds += 86400 
            time_taken_string = f"{total_seconds // 3600} H {(total_seconds % 3600) // 60} M"
            
        log_date = datetime.now().strftime("%Y-%m-%d")

        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE tracker_data 
            SET agent=%s, mls_name=%s, prop_type=%s, home_type=%s, listing_date=%s, status=%s, time_log=%s, log_date=%s
            WHERE hid=%s;
        """, (current_agent, data.get('mls_name'), data.get('prop_type'), data.get('home_type'), 
              data.get('listing_date'), data.get('status'), time_taken_string, log_date, hid))
        
        conn.commit()
        cur.close()
        conn.close()

        # Call your background thread here to also update Google Sheets!
        thread = threading.Thread(target=sync_to_sheets_task, args=(data, current_agent, time_taken_string, log_date))
        thread.start()

        return jsonify({"status": "success", "message": "Entry updated instantly!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/delete/<hid>', methods=['DELETE'])
@login_required
def delete_entry(hid):
    try:
        # 1. Delete instantly from PostgreSQL
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM tracker_data WHERE hid = %s;", (hid,))
        conn.commit()
        cur.close()
        conn.close()

        # 2. Delete from Google Sheets in the background
        def delete_from_sheets_bg(hid_to_delete):
            try:
                sheet = get_google_sheet()
                cell = sheet.find(str(hid_to_delete))
                if cell:
                    sheet.delete_rows(cell.row)
            except Exception as e:
                print(f"Background Sheet Delete Error: {e}")

        thread = threading.Thread(target=delete_from_sheets_bg, args=(hid,))
        thread.start()

        return jsonify({"status": "success", "message": f"HID {hid} deleted!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
@app.route('/import_sheets')
def import_sheets():
    try:
        sheet = get_google_sheet()
        all_records = sheet.get_all_values()[1:]

        conn = get_db_connection()
        cur = conn.cursor()

        imported_count = 0
        for row in all_records:
            # Ensure the row has enough columns to avoid index errors
            if len(row) < 9:
                row.extend([''] * (9 - len(row)))
                
            hid, agent, mls_name, prop_type, home_type, listing_date, status, time_log, log_date = row[:9]
            
            # Insert into database. If HID exists, skip it.
            cur.execute("""
                INSERT INTO tracker_data 
                (hid, agent, mls_name, prop_type, home_type, listing_date, status, time_log, log_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (hid) DO UPDATE SET 
                    agent = EXCLUDED.agent,
                    mls_name = EXCLUDED.mls_name,
                    prop_type = EXCLUDED.prop_type,
                    home_type = EXCLUDED.home_type,
                    listing_date = EXCLUDED.listing_date,
                    status = EXCLUDED.status,
                    time_log = EXCLUDED.time_log,
                    log_date = EXCLUDED.log_date;
            """, (hid, agent, mls_name, prop_type, home_type, listing_date, status, time_log, log_date))
            imported_count += 1
            
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({"status": "success", "message": f"Imported {imported_count} records from Google Sheets into PostgreSQL!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    # host='0.0.0.0' tells Flask to broadcast to your whole Wi-Fi network
    app.run(host='0.0.0.0', port=5000, debug=True)