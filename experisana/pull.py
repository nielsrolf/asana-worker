import os
import re
import asana
from urllib.parse import urlparse
from dotenv import load_dotenv
import requests
from asana.rest import ApiException
import backoff
from datetime import datetime

load_dotenv(override=True)

ACCESS_TOKEN = os.getenv("ASANA_ACCESS_TOKEN")

configuration = asana.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client = asana.ApiClient(configuration)
tasks_api_instance = asana.TasksApi(api_client)
attachments_api_instance = asana.AttachmentsApi(api_client)

opts = {
    'opt_fields': "name,notes,attachments,download_url,created_at",
}

def sanitize_filename(filename):
    return re.sub(r'[^\w\-_\. ]', '_', filename)

@backoff.on_exception(backoff.expo, ApiException, max_tries=5)
def get_task_details(task_gid):
    return tasks_api_instance.get_task(task_gid, opts)

@backoff.on_exception(backoff.expo, ApiException, max_tries=5)
def get_attachments(task_gid):
    return attachments_api_instance.get_attachments_for_object(task_gid, opts)

@backoff.on_exception(backoff.expo, ApiException, max_tries=5)
def get_attachment_details(attachment_gid):
    return attachments_api_instance.get_attachment(attachment_gid, opts)

def download_file(url, filepath):
    response = requests.get(url)
    with open(filepath, 'wb') as f:
        f.write(response.content)

def pull_attachments(master_task_url):
    # Extract task GID from URL
    parsed_url = urlparse(master_task_url)
    master_task_gid = parsed_url.path.split('/')[-1]

    # Get master task details
    master_task = get_task_details(master_task_gid)

    # Extract subtask GIDs from master task notes
    subtask_gids = re.findall(r'https://app\.asana\.com/0/\d+/(\d+)', master_task['notes'])

    for subtask_gid in subtask_gids:
        subtask = get_task_details(subtask_gid)
        folder_name = sanitize_filename(subtask['name'])
        os.makedirs(folder_name, exist_ok=True)

        attachments = get_attachments(subtask_gid)
        latest_attachments = {}

        for attachment in attachments:
            name = attachment.get('name', 'unnamed_file')
            created_at = attachment.get('created_at')
            attachment_gid = attachment.get('gid')

            if name not in latest_attachments or created_at > latest_attachments[name]['created_at']:
                latest_attachments[name] = {
                    'created_at': created_at,
                    'attachment_gid': attachment_gid
                }

        for name, attachment_info in latest_attachments.items():
            attachment_details = get_attachment_details(attachment_info['attachment_gid'])
            download_url = attachment_details.get('download_url')

            if not download_url:
                print(f"Warning: No download URL for attachment {name} in task {subtask['name']}. Skipping.")
                continue

            file_path = os.path.join(folder_name, name)
            download_file(download_url, file_path)
            print(f"Downloaded: {file_path}")

    print("All attachments have been downloaded.")