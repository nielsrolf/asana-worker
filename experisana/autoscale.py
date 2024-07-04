import os
import time
import random
import subprocess
import yaml
from dotenv import load_dotenv
import asana
from asana.rest import ApiException
import backoff
from experisana.worker import (
    PROJECT_GID,
    column_gids,
    tasks_api_instance,
    stories_api_instance,
    CONFIG,
    opts,
    move_task_to_column,
    upload_log_to_task
)

@backoff.on_exception(backoff.expo, ApiException, max_tries=5)
def count_available_tasks():
    tasks = tasks_api_instance.get_tasks_for_section(column_gids["Backlog"], {"limit": 100})
    return len(list(tasks))

@backoff.on_exception(backoff.expo, ApiException, max_tries=5)
def count_active_workers():
    tasks = tasks_api_instance.get_tasks_for_section(column_gids["Active Workers"], {"limit": 100})
    return len(list(tasks))

@backoff.on_exception(backoff.expo, ApiException, max_tries=5)
def create_worker_task(worker_id):
    task_data = {
        "data": {
            "name": f"Starting worker {worker_id}",
            "notes": f"Starting worker {worker_id}",
            "projects": [PROJECT_GID],
            "memberships": [{"project": PROJECT_GID, "section": column_gids["Active Workers"]}]
        }
    }
    return tasks_api_instance.create_task(task_data, opts)

@backoff.on_exception(backoff.expo, ApiException, max_tries=5)
def post_comment_to_task(task_gid, comment_text):
    if len(comment_text) > 2000:
        with open(f'/tmp/logs-{task_gid}', 'w') as f:
            f.write(comment_text)
        upload_log_to_task(task_gid, f'/tmp/logs-{task_gid}')
        comment_text = comment_text[:2000]
        comment_text += "Comment too long. See attached file."
    body = {"data": {"text": comment_text}}
    stories_api_instance.create_story_for_task(body, task_gid, opts)


def scale_up():
    worker_id = f"worker-{int(time.time())}"
    task = create_worker_task(worker_id)
    
    try:
        result = subprocess.run(CONFIG['scale']['cmd'], shell=True, check=True, capture_output=True, text=True)
        status = 'succeeded'
        output = result.stdout
        target_column = column_gids["Done"]
    except subprocess.CalledProcessError as e:
        status = 'failed'
        output = e.stderr
        target_column = column_gids["Failed"]

    comment_text = f"Scale up {status}. Logs:\n```\n{output}\n```"
    post_comment_to_task(task['gid'], comment_text)
    
    # Move the task to the appropriate column (Done or Failed)
    move_task_to_column(task['gid'], target_column)

def autoscale():
    while True:
        available_tasks = count_available_tasks()
        active_workers = count_active_workers()
        
        if active_workers < CONFIG['scale']['max'] and available_tasks > active_workers:
            scale_up()
            wait_time = random.randint(0, CONFIG['scale'].get('wait_between_scales_min', 1) * 60)
            time.sleep(wait_time)
        else:
            time.sleep(5)

if __name__ == "__main__":
    autoscale()