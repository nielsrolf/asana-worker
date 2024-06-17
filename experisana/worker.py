import asana
import os
import subprocess
from datetime import datetime
from asana.rest import ApiException
import time
from dotenv import load_dotenv
import requests

load_dotenv()

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
    'opt_fields': "actual_time_minutes,approval_status,assignee,assignee.name,assignee_section,assignee_section.name,assignee_status,completed,completed_at,completed_by,completed_by.name,created_at,created_by,custom_fields,custom_fields.asana_created_field,custom_fields.created_by,custom_fields.created_by.name,custom_fields.currency_code,custom_fields.custom_label,custom_fields.custom_label_position,custom_fields.date_value,custom_fields.date_value.date,custom_fields.date_value.date_time,custom_fields.description,custom_fields.display_value,custom_fields.enabled,custom_fields.enum_options,custom_fields.enum_options.color,custom_fields.enum_options.enabled,custom_fields.enum_options.name,custom_fields.enum_value,custom_fields.enum_value.color,custom_fields.enum_value.enabled,custom_fields.enum_value.name,custom_fields.format,custom_fields.has_notifications_enabled,custom_fields.id_prefix,custom_fields.is_formula_field,custom_fields.is_global_to_workspace,custom_fields.is_value_read_only,custom_fields.multi_enum_values,custom_fields.multi_enum_values.color,custom_fields.multi_enum_values.enabled,custom_fields.multi_enum_values.name,custom_fields.name,custom_fields.number_value,custom_fields.people_value,custom_fields.people_value.name,custom_fields.precision,custom_fields.representation_type,custom_fields.resource_subtype,custom_fields.text_value,custom_fields.type,dependencies,dependents,due_at,due_on,external,external.data,followers,followers.name,hearted,hearts,hearts.user,hearts.user.name,html_notes,is_rendered_as_separator,liked,likes,likes.user,likes.user.name,memberships,memberships.project,memberships.project.name,memberships.section,memberships.section.name,modified_at,name,notes,num_hearts,num_likes,num_subtasks,parent,parent.created_by,parent.name,parent.resource_subtype,permalink_url,projects,projects.name,resource_subtype,start_at,start_on,tags,tags.name,workspace,workspace.name", # list[str] | This endpoint returns a compact resource, which excludes some properties by default. To include those optional properties, set this query parameter to a comma-separated list of the properties you wish to include.
}

# Set up Asana API client
configuration = asana.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client = asana.ApiClient(configuration)
tasks_api_instance = asana.TasksApi(api_client)
sections_api_instance = asana.SectionsApi(api_client)
attachments_api_instance = asana.AttachmentsApi(api_client)
stories_api_instance = asana.StoriesApi(api_client)

def get_column_gids():
    try:
        sections = sections_api_instance.get_sections_for_project(PROJECT_GID, opts=opts)
        column_gids = {}
        for section in sections:
            column_gids[section['name']] = section['gid']
        return column_gids
    except ApiException as e:
        print("Exception when fetching project sections: %s\n" % e)
        return {}
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

def get_task_details(task_gid):
    task = tasks_api_instance.get_task(task_gid, opts)
    return task

def get_task_dependencies(task):
    dependencies = []
    if 'notes' in task:
        notes_lines = task['notes'].split('\n')
        for line in notes_lines:
            if line.startswith('- '):
                dependency = line.split('/')[-1].replace(')', '')
                dependencies.append(dependency)
    return dependencies

def is_task_done(task_gid, done_column_gid):
    try:
        task = get_task_details(task_gid)
        if task is None:
            return False
        return task['memberships'][0]['section']['gid'] == done_column_gid
    except ApiException as e:
        print("Exception when checking task status: %s\n" % e)
    return False

def get_backlog_task(backlog_column_gid, done_column_gid):
    tasks = tasks_api_instance.get_tasks_for_section(backlog_column_gid, {"limit": 1})
    for task in tasks:
        try:
            task_details = get_task_details(task['gid'])
            dependencies = get_task_dependencies(task_details)
            if all(is_task_done(dep, done_column_gid) for dep in dependencies):
                return task_details
        except ApiException as e:
            print("Exception when calling get_backlog_task: %s\n" % e)
    return None

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
        print("Exception when calling SectionsApi->add_task_for_section: %s\n" % e)

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
        print("Exception when calling Asana API via curl: %s\n" % e)

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
    except ApiException as e:
        print("Exception when calling AttachmentsApi->get_attachments_for_object: %s\n" % e)
    except Exception as e:
        print("Exception when downloading attachment: %s\n" % e)
    return False

def post_comment_to_task(task_gid, comment_text):
    body = {"data": {"text": comment_text}}
    try:
        stories_api_instance.create_story_for_task(body, int(task_gid), opts)
    except ApiException as e:
        print("Exception when calling StoriesApi->create_story_for_task: %s\n" % e)

def run_experiment(task, column_gids):
    print(f"Running experiment: {task['name']}")
    task_gid = task['gid']
    command = task['notes'].strip().split("# Depends on")[0].strip()

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
            result = subprocess.run(command, shell=True, check=True, stdout=log_file, stderr=subprocess.STDOUT)
            move_task_to_column(task_gid, column_gids["Done"])
            status = 'succeeded'
        except subprocess.CalledProcessError:
            move_task_to_column(task_gid, column_gids["Failed"])
            status = 'failed'

    # Read the first 100 lines of the log file and post as a comment
    try:
        with open(log_file_path, "r") as log_file:
            log_lines = log_file.readlines()
            comment_text = f'Status: {status} with logs:\n```\n' + ''.join(log_lines[:100]) + '\n```\n'
            if len(log_lines) > 100:
                comment_text += f'... and {len(log_lines) - 100} more lines'
                upload_log_to_task(task_gid, log_file_path)
            post_comment_to_task(task_gid, comment_text)
    except Exception as e:
        print("Exception when reading log file or posting comment: %s\n" % e)

    # Upload all uploads in the task_directory/uploads directory
    for root, dirs, files in os.walk(os.path.join(task_dir, "uploads")):
        for file in files:
            try:
                subprocess.run([
                    'curl', '--request', 'POST',
                    '--url', 'https://app.asana.com/api/1.0/attachments?opt_fields=',
                    '--header', 'accept: application/json',
                    '--header', f'Authorization: Bearer {ACCESS_TOKEN}',
                    '--header', 'content-type: multipart/form-data',
                    '--form', f'resource_subtype=asana',
                    '--form', f'file=@{os.path.join(root, file)}',
                    '--form', f'parent={task_gid}'
                ], check=True)
            except subprocess.CalledProcessError as e:
                print("Exception when calling Asana API via curl: %s\n" % e)

    # Change back to the original working directory
    os.chdir(original_cwd)

def main():
    worker_id = get_or_create_worker_id()

    if not column_gids:
        print("Failed to fetch column GIDs")
        return

    while True:
        task = get_backlog_task(column_gids["Backlog"], column_gids["Done"])
        if task:
            run_experiment(task, column_gids)
        else:
            time.sleep(5)  # Sleep for 5 seconds before checking again

if __name__ == "__main__":
    main()
