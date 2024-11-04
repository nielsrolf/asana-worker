import os
import json
import re
import asana
from urllib.parse import urlparse
from dotenv import load_dotenv
import requests
from asana.rest import ApiException
import backoff
from datetime import datetime
import argparse

from experisana.schedule import process_yaml

load_dotenv(override=True)

ACCESS_TOKEN = os.getenv("ASANA_ACCESS_TOKEN")
WORKSPACE_GID = os.getenv("ASANA_WORKSPACE_GID")
PROJECT_GID = os.getenv("ASANA_PROJECT_GID")

configuration = asana.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client = asana.ApiClient(configuration)
tasks_api_instance = asana.TasksApi(api_client)
attachments_api_instance = asana.AttachmentsApi(api_client)
tags_api_instance = asana.TagsApi(api_client)

opts = {
    'opt_fields': "name,notes,attachments,download_url,created_at,tags,tags.name",
}

def sanitize_filename(filename):
    return re.sub(r'[^\w\-_\. ]', '_', filename.split('/')[-1])

@backoff.on_exception(backoff.expo, ApiException, max_tries=5)
def get_task_details(task_gid):
    return tasks_api_instance.get_task(task_gid, opts)

@backoff.on_exception(backoff.expo, ApiException, max_tries=5)
def get_attachments(task_gid):
    return attachments_api_instance.get_attachments_for_object(task_gid, opts)

@backoff.on_exception(backoff.expo, ApiException, max_tries=5)
def get_attachment_details(attachment_gid):
    return attachments_api_instance.get_attachment(attachment_gid, opts)

@backoff.on_exception(backoff.expo, ApiException, max_tries=5)
def get_tasks_by_tag(tag_name):
    # First, find the tag GID
    tags = tags_api_instance.get_tags({'workspace': WORKSPACE_GID, 'name': tag_name})
    tag_gid = next((tag['gid'] for tag in tags if tag['name'] == tag_name), None)
    
    if not tag_gid:
        print(f"No tag found with name: {tag_name}")
        return []

    # Then, get tasks with this tag and in the specified project
    tasks = tasks_api_instance.get_tasks({
        # 'project': PROJECT_GID,
        # 'workspace': WORKSPACE_GID,
        'tag': tag_gid,
        'opt_fields': 'name,notes,attachments'
    })
    return list(tasks)

def download_file(url, filepath):
    response = requests.get(url)
    with open(filepath, 'wb') as f:
        f.write(response.content)

def get_contexts_from_master_task(master_task_gid):
    tasks_cmd_and_context = {}
    attachments = get_attachments(master_task_gid)
    for attachment in attachments:
        name = attachment.get('name', 'unnamed_file')
        download_url = attachment.get('download_url')
        file_path = os.path.join('stages.yaml')
        download_file(download_url, file_path)
        print(f"Downloaded: {file_path}")
        try:
            tasks_cmd_and_context.update(process_yaml(file_path, True, True))
        except Exception as e:
            pass
    return tasks_cmd_and_context

def pull_attachments(tag=None, url=None):
    if tag:
        tasks = get_tasks_by_tag(tag)
        tasks_cmd_and_context = {}
    elif url:
        # Extract task GID from URL
        parsed_url = urlparse(url)
        master_task_gid = parsed_url.path.split('/')[-1]
        master_task = get_task_details(master_task_gid)
        # Reconstruct context and cmd from subtasks
        tasks_cmd_and_context = get_contexts_from_master_task(master_task_gid)
        # Extract subtask GIDs from master task notes
        subtask_gids = re.findall(r'https://app\.asana\.com/0/\d+/(\d+)', master_task['notes'])
        tasks = [get_task_details(gid) for gid in subtask_gids]
    else:
        # This should never happen due to the argument parser, but just in case:
        raise ValueError("Either tag or master_task_url must be provided.")

    if not tasks:
        print("No tasks found.")
        return


    for task in tasks:
        folder_name = sanitize_filename(task['name'])
        os.makedirs(folder_name, exist_ok=True)

        # Write task description to cmd.sh
        with open(os.path.join(folder_name, 'cmd.sh'), 'w') as f:
            f.write(task['notes'])

        attachments = get_attachments(task['gid'])
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
                print(f"Warning: No download URL for attachment {name} in task {task['name']}. Skipping.")
                continue

            file_path = os.path.join(folder_name, name)
            download_file(download_url, file_path)
            print(f"Downloaded: {file_path}")

        # Write cmd and context to file if available
        if task['name'] in tasks_cmd_and_context:
            with open(os.path.join(folder_name, 'cmd.sh'), 'w') as f:
                f.write(tasks_cmd_and_context[task['name']]['cmd'])

            with open(os.path.join(folder_name, 'context.json'), 'w') as f:
                f.write(json.dumps(tasks_cmd_and_context[task['name']]['context'], indent=4))

    print("All attachments have been downloaded.")

def main():
    parser = argparse.ArgumentParser(description='Pull attachments from Asana tasks.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-t', '--tag', help='Tag to filter tasks')
    group.add_argument('-u', '--url', help='URL of the master task')
    args = parser.parse_args()
    pull_attachments(tag=args.tag, master_task_url=args.url)

if __name__ == "__main__":
    main()