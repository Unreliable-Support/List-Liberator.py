# --- START OF FILE List-Liberator.py ---

import os
import pickle
import re
import time  # Added for handling rate limits
from datetime import datetime
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.pickle.
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service():
    """Authenticates and returns the Gmail API service.
    Returns None if authentication fails, e.g., if credentials.json is missing.
    """
    creds = None
    if os.path.exists('token.pickle'):
        try:
            with open('token.pickle', 'rb') as token:
                creds = pickle.load(token)
        except (pickle.UnpicklingError, EOFError):
             print("Warning: 'token.pickle' file is corrupted. It will be recreated.")
             creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Failed to refresh token: {e}. Please re-authenticate.")
                creds = None
        
        if not creds:
            try:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            except FileNotFoundError:
                print("--- CRITICAL ERROR ---")
                print("The 'credentials.json' file was not found in the same directory as the script.")
                print("Please follow the setup instructions in the README.md to get this file.")
                return None

        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
            
    try:
        service = build('gmail', 'v1', credentials=creds)
        return service
    except Exception as e:
        print(f"Failed to build Gmail service: {e}")
        return None

def extract_unsubscribe_url(header_value):
    """Extracts the URL from the List-Unsubscribe header."""
    match = re.search(r'<(https?://[^>]+)>', header_value)
    return match.group(1) if match else None

def batch_get_messages(service, msg_ids):
    """Fetches multiple emails using a batch request for efficiency."""
    batch = service.new_batch_http_request()
    responses = []
    
    def callback(request_id, response, exception):
        if exception:
            # We will handle HttpError 429 specifically, but print others
            if not isinstance(exception, HttpError) or exception.resp.status != 429:
                 print(f"Error in batch request for ID {request_id}: {exception}")
        else:
            responses.append(response)
            
    for msg_id in msg_ids:
        batch.add(service.users().messages().get(userId='me', id=msg_id, format='full'), callback=callback)
    
    batch.execute()
    return responses

def main():
    try:
        service = get_gmail_service()
        
        if not service:
            return

        print("Successfully connected to the Gmail service.")
        
        subscriptions = {}
        query = 'label:promotions unsubscribe'
        page_token = None
        msg_ids = []
        
        print("Searching for all matching emails... (this may take a while for large inboxes)")
        while True:
            results = service.users().messages().list(userId='me', q=query, pageToken=page_token).execute()
            messages = results.get('messages', [])
            if not messages:
                break
            msg_ids.extend([msg['id'] for msg in messages])
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        
        if not msg_ids:
            print("No emails matching the 'promotions' and 'unsubscribe' query were found.")
            return

        print(f"{len(msg_ids)} total emails found. Processing them now...")
        
        # Reduced batch size and added delays to avoid rate limit errors
        batch_size = 50 
        for i in range(0, len(msg_ids), batch_size):
            batch_ids = msg_ids[i:i + batch_size]
            print(f"Processing email batch {i+1}-{i+len(batch_ids)} of {len(msg_ids)}...")
            messages_data = batch_get_messages(service, batch_ids)
            
            for message in messages_data:
                if 'payload' not in message or 'headers' not in message['payload']:
                    continue
                headers = message['payload']['headers']
                unsubscribe_header = next((h['value'] for h in headers if h['name'].lower() == 'list-unsubscribe'), None)
                
                if unsubscribe_header:
                    unsubscribe_url = extract_unsubscribe_url(unsubscribe_header)
                    if unsubscribe_url:
                        sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown Sender')
                        subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
                        date = datetime.fromtimestamp(int(message['internalDate']) / 1000)
                        
                        clean_sender = sender.split('<')[0].strip().replace('"', '')
                        if clean_sender not in subscriptions or date > datetime.strptime(subscriptions[clean_sender]['date'], '%Y-%m-%d %H:%M:%S'):
                            subscriptions[clean_sender] = {
                                'sender_full': sender,
                                'subject': subject,
                                'date': date.strftime('%Y-%m-%d %H:%M:%S'),
                                'unsubscribe': unsubscribe_url
                            }
            
            # Pause for 1 second between batches to respect API rate limits
            time.sleep(1)

        if subscriptions:
            sorted_subscriptions = sorted(subscriptions.items(), key=lambda item: item[0].lower())
            
            with open('subscriptions.html', 'w', encoding='utf-8') as f:
                f.write('<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Email Subscriptions - List-Liberator</title>')
                f.write('<style>body{font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 2em;} table{border-collapse: collapse; width: 100%; box-shadow: 0 2px 5px rgba(0,0,0,0.1);} th, td{border: 1px solid #dddddd; text-align: left; padding: 12px;} th{background-color: #f8f8f8;} tr:nth-child(even){background-color: #f2f2f2;} tr:hover{background-color: #e9e9e9;} a{color: #d93025; font-weight: bold; text-decoration: none;} a:hover{text-decoration: underline;}</style>')
                f.write('</head><body><h2>Found Subscriptions</h2><p>Click the link in the "Unsubscribe" column to opt-out from a mailing list.</p><table>')
                f.write('<tr><th>Sender</th><th>Last Subject</th><th>Last Date</th><th>Unsubscribe Link</th></tr>')
                for _, sub in sorted_subscriptions:
                    f.write(f'<tr><td>{sub["sender_full"]}</td><td>{sub["subject"]}</td><td>{sub["date"]}</td><td><a href="{sub["unsubscribe"]}" target="_blank" rel="noopener noreferrer">Unsubscribe</a></td></tr>')
                f.write('</table></body></html>')
            print(f'\n--- SUCCESS ---')
            print(f'{len(sorted_subscriptions)} unique subscriptions found.')
            print('An HTML file named "subscriptions.html" has been created.')
            print('Open it in your web browser to manage your subscriptions.')
        else:
            print("\nNo emails with valid unsubscribe links were found in the searched messages.")
    
    except HttpError as e:
        print(f"\n--- GMAIL API ERROR ---")
        print(f"An error occurred while communicating with Gmail: {e}")
        print("This could be due to permission issues, rate limits, or a temporary Google server problem.")
    except Exception as e:
        print(f"\n--- UNEXPECTED ERROR ---")
        print(f"An unexpected error occurred: {e}")
    finally:
        print("\n----------------------------------------------------")
        input("Script has finished. Press Enter to close this window...")

if __name__ == '__main__':
    main()

# --- END OF FILE ---
