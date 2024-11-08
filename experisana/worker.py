import random
import asana
import os
import subprocess
from datetime import datetime
import time
from dotenv import load_dotenv
import requests
from requests.exceptions import RequestException
from asana.rest import ApiException
import backoff
import yaml
import threading

load_dotenv(override=True)

# Configuration
ACCESS_TOKEN = os.getenv("ASANA_ACCESS_TOKEN")
WORKSPACE_GID = os.getenv("ASANA_WORKSPACE_GID")
PROJECT_GID = os.getenv("ASANA_PROJECT_GID")

expected_envs = [
    "ASANA_ACCESS_TOKEN",
    "ASANA_WORKSPACE_GID",
    "ASANA_PROJECT_GID"
]
if not all([os.getenv(i) for i in expected_envs]):
    missing = [i for i in expected_envs if not os.getenv(i)]
    raise Exception(f"Missing environment variables: {missing}")

opts = {
    'opt_fields': "actual_time_minutes,approval_status,assignee,assignee.name,assignee_section,assignee_section.name,assignee_status,completed,completed_at,completed_by,completed_by.name,created_at,created_by,custom_fields,custom_fields.asana_created_field,custom_fields.created_by,custom_fields.created_by.name,custom_fields.currency_code,custom_fields.custom_label,custom_fields.custom_label_position,custom_fields.date_value,custom_fields.date_value.date,custom_fields.date_value.date_time,custom_fields.description,custom_fields.display_value,custom_fields.enabled,custom_fields.enum_options,custom_fields.enum_options.color,custom_fields.enum_options.enabled,custom_fields.enum_options.name,custom_fields.enum_value,custom_fields.enum_value.color,custom_fields.enum_value.enabled,custom_fields.enum_value.name,custom_fields.format,custom_fields.has_notifications_enabled,custom_fields.id_prefix,custom_fields.is_formula_field,custom_fields.is_global_to_workspace,custom_fields.is_value_read_only,custom_fields.multi_enum_values,custom_fields.multi_enum_values.color,custom_fields.multi_enum_values.enabled,custom_fields.multi_enum_values.name,custom_fields.name,custom_fields.number_value,custom_fields.people_value,custom_fields.people_value.name,custom_fields.precision,custom_fields.representation_type,custom_fields.resource_subtype,custom_fields.text_value,custom_fields.type,dependencies,dependents,due_at,due_on,external,external.data,followers,followers.name,hearted,hearts,hearts.user,hearts.user.name,html_notes,is_rendered_as_separator,liked,likes,likes.user,likes.user.name,memberships,memberships.project,memberships.project.name,memberships.section,memberships.section.name,modified_at,name,notes,num_hearts,num_likes,num_subtasks,parent,parent.created_by,parent.name,parent.resource_subtype,permalink_url,projects,projects.name,resource_subtype,start_at,start_on,tags,tags.name,workspace,workspace.name",
}

# Set up Asana API client
configuration = asana.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client = asana.ApiClient(configuration)
tasks_api_instance = asana.TasksApi(api_client)
sections_api_instance = asana.SectionsApi(api_client)
attachments_api_instance = asana.AttachmentsApi(api_client)
stories_api_instance = asana.StoriesApi(api_client)


def load_config():
    """Check for experisana.yaml in [./, ../, ...]"""
    cwd = os.getcwd()
    config = {}
    while cwd != "/":
        config_path = os.path.join(cwd, "experisana.yaml")
        if os.path.exists(config_path):
            config = yaml.safe_load(open(config_path, "r"))
        cwd = os.path.dirname(cwd)

    # A bit of custom logic for imo-experiment
    for config_path in ["/workspace/experisana.yaml", "/Users/nielswarncke/Documents/code/asana-worker/experisana.yaml"]:
        if os.path.exists(config_path):
            config = yaml.safe_load(open(config_path, "r"))

    # Initialize cache if not present
    if 'cache' not in config:
        config['cache'] = {
            'base_model': [],
            'model_id': []
        }
    return config
CONFIG = load_config()

@backoff.on_exception(backoff.expo, (ApiException), max_tries=100)
def get_column_gids():
    try:
        sections = sections_api_instance.get_sections_for_project(PROJECT_GID, opts=opts)
        column_gids = {}
        for section in sections:
            column_gids[section['name']] = section['gid']
        return column_gids
    except ApiException as e:
        print(f"Exception when fetching project sections: {e}")
        raise

column_gids = get_column_gids()

BACKLOG_COLUMN_GID = column_gids.get("Backlog", None)

def get_or_create_worker_id():
    worker_id_path = os.path.expanduser("~/worker_id")
    if os.path.exists(worker_id_path):
        with open(worker_id_path, "r") as f:
            worker_id = f.read().strip()
    else:
        worker_id = f"worker-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        with open(worker_id_path, "w") as f:
            f.write(worker_id)
    return worker_id

@backoff.on_exception(backoff.expo, (ApiException, RequestException), max_tries=100)
def get_task_details(task_gid):
    return tasks_api_instance.get_task(task_gid, opts)

def get_task_dependencies(task):
    dependencies = []
    if 'notes' in task:
        notes_lines = task['notes'].split('\n')
        for line in notes_lines:
            if line.startswith('- '):
                dependency = line.split('/')[-1].replace(')', '')
                dependencies.append(dependency)
    return dependencies

@backoff.on_exception(backoff.expo, (ApiException, RequestException), max_tries=100)
def is_task_done(task_gid, done_column_gid):
    try:
        task = get_task_details(task_gid)
        if task is None:
            return False
        return task['memberships'][0]['section']['gid'] == done_column_gid
    except ApiException as e:
        print(f"Exception when checking task status: {e}")
        raise

import json
import re
from typing import Dict, Optional, List

def extract_context_from_notes(notes: str) -> Optional[Dict]:
    """Extract the JSON context from task notes."""
    context_match = re.search(r'# Context\n```json\n(.*?)\n```', notes, re.DOTALL)
    if context_match:
        try:
            return json.loads(context_match.group(1))
        except json.JSONDecodeError:
            return None
    return None

def calculate_cache_score(task_context: Dict, worker_cache: Dict[str, List[str]]) -> int:
    """Calculate how many cached items match the task context."""
    if not task_context:
        return 0
    
    score = 0
    for cache_key, cached_values in worker_cache.items():
        if cache_key in task_context and task_context[cache_key] in cached_values:
            score += 1
    return score

def update_cache(context: Dict, config: Dict) -> Dict:
    """Update the worker's cache with new values from the task context."""
    for cache_key in config['cache'].keys():
        if cache_key in context:
            if context[cache_key] not in config['cache'][cache_key]:
                config['cache'][cache_key].append(context[cache_key])
    
    # Save the updated config
    config_path = os.path.join(os.getcwd(), "experisana.yaml")
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    
    return config

@backoff.on_exception(backoff.expo, (ApiException, RequestException), max_tries=5)
def get_backlog_task(backlog_column_gid, done_column_gid):
    tasks = tasks_api_instance.get_tasks_for_section(backlog_column_gid, {"limit": 1})
    
    # Score each task based on cache hits
    scored_tasks = []
    for task in tasks:
        try:
            task_details = get_task_details(task['gid'])
            dependencies = get_task_dependencies(task_details)
            
            # Skip tasks with unmet dependencies
            if not all(is_task_done(dep, done_column_gid) for dep in dependencies):
                continue
                
            # Extract context and calculate cache score
            context = extract_context_from_notes(task_details['notes'])
            score = calculate_cache_score(context, CONFIG['cache'])
            scored_tasks.append((score, task_details))
            
        except ApiException as e:
            print(f"Exception when scoring task: {e}")
            continue
    
    if not scored_tasks:
        return None
    
    # Sort by score (highest first) and randomly select among tasks with the same highest score
    scored_tasks.sort(key=lambda x: x[0], reverse=True)
    highest_score = scored_tasks[0][0]
    best_tasks = [task for score, task in scored_tasks if score == highest_score]
    
    return random.choice(best_tasks)


@backoff.on_exception(backoff.expo, (ApiException, RequestException), max_tries=5)
def assign_task_to_worker(task_gid, worker_id):
    try:
        # Get the current task details
        task = get_task_details(task_gid)
        
        # Update the task notes with the worker assignment
        updated_notes = task['notes'].strip() + f"\n# Assigned to: {worker_id}"
        
        update_data = {
            "data": {
                "notes": updated_notes
            }
        }
        
        # Update the task
        updated_task = tasks_api_instance.update_task(update_data, task_gid, opts)
        
        # Move the task to the Running column
        move_task_to_column(task_gid, column_gids["Running"])
        
        # Check if the assignment was successful
        final_task = get_task_details(task_gid)
        if final_task['notes'].strip().endswith(f"# Assigned to: {worker_id}"):
            return True
        else:
            return False
    except ApiException as e:
        print(f"Exception when assigning task to worker: {e}")
        raise

def check_task_status(task_gid, running_column_gid, stop_event):
    while not stop_event.is_set():
        time.sleep(60)  # Check every minute
        try:
            task = get_task_details(task_gid)
            if task['memberships'][0]['section']['gid'] != running_column_gid:
                stop_event.set()
                print(f"Task {task_gid} was moved out of the Running column. Interrupting execution.")
                break
            else:
                print('.', end='')
        except Exception as e:
            print(f"Error checking task status: {e}")

def run_experiment(task, column_gids, worker_id):
    print(f"Running experiment: {task['name']}")
    task_gid = task['gid']
    
    # Try to assign the task to this worker
    if not assign_task_to_worker(task_gid, worker_id):
        print(f"Task {task_gid} was assigned to another worker. Skipping.")
        return False
    
    # Extract context and update cache before running
    context = extract_context_from_notes(task['notes'])
    if context:
        global CONFIG
        CONFIG = update_cache(context, CONFIG)

    command = task['notes'].strip().split("# Depends on")[0].strip().split("# Assigned to:")[0].strip()
    # Prepend 'set -e' to ensure the shell exits if any command fails
    command = f"set -e; {command}"

    # Create a new directory for the task
    task_dir = os.path.join("/tmp", f"task_{task_gid}_{datetime.now().strftime('%Y%m%d%H%M%S')}")
    os.makedirs(task_dir, exist_ok=True)

    # Download attachments to the new directory
    if not download_attachments(task_gid, task_dir):
        print("Failed to download attachments")
        return

    move_task_to_column(task_gid, column_gids["Running"])

    log_file_path = os.path.join(task_dir, "experiment_logs.txt")

    # Change the current working directory to the new task directory
    original_cwd = os.getcwd()
    os.chdir(task_dir)

    print(f"Use the following command to watch logs:\n    watch tail {log_file_path}")
    with open(log_file_path, "w") as log_file:
        try:
            # Create a stop event and start the status checking thread
            stop_event = threading.Event()
            status_thread = threading.Thread(target=check_task_status, args=(task_gid, column_gids["Running"], stop_event))
            status_thread.start()

            print(f"Running command: {command}")

            # Run the command with a timeout
            process = subprocess.Popen(command, shell=True, stdout=log_file, stderr=subprocess.STDOUT)
            
            while process.poll() is None:
                if stop_event.is_set():
                    process.terminate()
                    process.wait(timeout=10)
                    status = 'interrupted'
                    break
                time.sleep(1)
            else:
                status = 'succeeded' if process.returncode == 0 else 'failed'

            # Stop the status checking thread
            stop_event.set()
            status_thread.join()

            if status == 'succeeded':
                move_task_to_column(task_gid, column_gids["Done"])
            elif status == 'failed':
                move_task_to_column(task_gid, column_gids["Failed"])
            # If interrupted, we don't move the task

        except Exception as e:
            print(f"Exception during experiment execution: {e}")
            status = 'failed'
            move_task_to_column(task_gid, column_gids["Failed"])

    # Read the first 100 lines of the log file and post as a comment
    try:
        with open(log_file_path, "r") as log_file:
            log_lines = log_file.readlines()
            comment_text = f'Status: {status} with logs:\n' + ''.join(log_lines[:100])
            if len(log_lines) > 100:
                comment_text += f'... and {len(log_lines) - 100} more lines'
                upload_log_to_task(task_gid, log_file_path)
            post_comment_to_task(task_gid, comment_text)
    except Exception as e:
        print(f"Exception when reading log file or posting comment: {e}")

    # Upload all uploads in the task_directory/uploads directory
    for root, dirs, files in os.walk(os.path.join(task_dir, "uploads")):
        for file in files:
            try:
                upload_log_to_task(task_gid, os.path.join(root, file))
            except Exception as e:
                print(f"Exception when uploading file {file}: {e}")

    # Change back to the original working directory
    os.chdir(original_cwd)
    return status != 'interrupted'



@backoff.on_exception(backoff.expo, (ApiException, RequestException), max_tries=5)
def move_task_to_column(task_gid, section_gid):
    opts = {
        'body': {
            "data": {
                'task': task_gid,
            }
        }
    }
    try:
        sections_api_instance.add_task_for_section(section_gid, opts)
    except ApiException as e:
        print(f"Exception when calling SectionsApi->add_task_for_section: {e}")
        raise

@backoff.on_exception(backoff.expo, (RequestException, subprocess.CalledProcessError), max_tries=5)
def upload_log_to_task(task_gid, log_file_path):
    try:
        subprocess.run([
            'curl', '--request', 'POST',
            '--url', 'https://app.asana.com/api/1.0/attachments?opt_fields=',
            '--header', 'accept: application/json',
            '--header', f'Authorization: Bearer {ACCESS_TOKEN}',
            '--header', 'content-type: multipart/form-data',
            '--form', f'resource_subtype=asana',
            '--form', f'file=@{log_file_path}',
            '--form', f'parent={task_gid}'
        ], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Exception when calling Asana API via curl: {e}")
        raise

@backoff.on_exception(backoff.expo, (ApiException, RequestException), max_tries=5)
def download_attachments(task_gid, download_dir):
    opts = {
        'opt_fields': "download_url,name",
        'limit': 50
    }
    try:
        attachments = attachments_api_instance.get_attachments_for_object(task_gid, opts)
        for attachment in attachments:
            download_url = attachment['download_url']
            file_name = attachment['name']
            response = requests.get(download_url)
            with open(os.path.join(download_dir, file_name), 'wb') as f:
                f.write(response.content)
        return True
    except (ApiException, RequestException) as e:
        print(f"Exception when downloading attachments: {e}")
        raise
    except Exception as e:
        print(f"Unexpected exception when downloading attachment: {e}")
    return False

@backoff.on_exception(backoff.expo, (ApiException, RequestException), max_tries=5)
def post_comment_to_task(task_gid, comment_text):
    body = {"data": {"text": comment_text}}
    try:
        stories_api_instance.create_story_for_task(body, int(task_gid), opts)
    except ApiException as e:
        print(f"Exception when calling StoriesApi->create_story_for_task: {e}")
        raise


@backoff.on_exception(backoff.expo, (ApiException, RequestException), max_tries=5)
def create_worker_task(worker_id):
    try:
        task_data = {
            "data": {
                "name": worker_id,
                "notes": worker_id,
                "projects": [PROJECT_GID],
                "memberships": [{"project": PROJECT_GID, "section": BACKLOG_COLUMN_GID}]
            }
        }
        task = tasks_api_instance.create_task(task_data, opts)
        move_task_to_column(task['gid'], column_gids["Active Workers"])
        return task
    except ApiException as e:
        print(f"Exception when calling TasksApi->create_task: {e}")
        raise

@backoff.on_exception(backoff.expo, (ApiException, RequestException), max_tries=5)
def delete_worker_task(task_gid):
    try:
        tasks_api_instance.delete_task(task_gid)
    except ApiException as e:
        print(f"Exception when calling TasksApi->delete_task: {e}")
        raise


def maybe_shutdown(idle_since, worker_id, worker_task):
    try:
        shutdown_after_minutes = CONFIG['shutdown']['after_idle_minutes']
        shutdown_cmd = CONFIG['shutdown']['cmd'].format(worker_id=worker_id)
    except KeyError:
        # We remain active if the config is missing
        return
    idle_seconds = (datetime.now() - idle_since).total_seconds()
    print(f"Idle for {idle_seconds} seconds. Shutting down after {shutdown_after_minutes} minutes of inactivity via '{shutdown_cmd}'")
    if idle_seconds > 60 * shutdown_after_minutes:
        print("Shutting down worker due to inactivity")
        delete_worker_task(worker_task['gid'])
        os.system(shutdown_cmd)
        exit(0)

def main(worker_id=None):
    worker_id = worker_id or get_or_create_worker_id()
    worker_task = create_worker_task(worker_id)

    if not column_gids:
        print("Failed to fetch column GIDs")
        return

    idle_since = datetime.now()
    while True:
        try:
            task = get_backlog_task(column_gids["Backlog"], column_gids["Done"])
            if task:
                task_completed = run_experiment(task, column_gids, worker_id)
                if task_completed:
                    idle_since = datetime.now()
                else:
                    print("Task was interrupted. Checking backlog again.")
            else:
                maybe_shutdown(idle_since, worker_id, worker_task)
                time.sleep(5) 
        except KeyboardInterrupt:
            print("Exiting")
            delete_worker_task(worker_task['gid'])
            exit(0)
        except Exception as e:
            print(f"Unexpected error in main loop: {e}")
            time.sleep(60)  # Sleep for 1 minute before retrying

if __name__ == "__main__":
    main()
