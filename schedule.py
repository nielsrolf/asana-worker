import yaml
import re
from typing import List, Dict
from worker import *
import random
import fire


def load_yaml(file_path: str) -> Dict:
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)

def substitute_variables(value: str, context: Dict[str, str]) -> str:
    return value.format(**context)

def resolve_dependencies(value: str, jobs_context: Dict[str, Dict[str, str]]) -> str:
    pattern = re.compile(r'\$\((\w+)\.(\w+)\)')
    return pattern.sub(lambda match: jobs_context[match.group(1)][match.group(2)], value)

from typing import List
tags_api_instance = asana.TagsApi(api_client)

  
job_name_to_gid = {}

def random_color() -> str:
    colors = 'dark-blue, dark-brown, dark-green, dark-orange, dark-pink, dark-purple, dark-red, dark-teal, dark-warm-gray, light-blue, light-green, light-orange, light-pink, light-purple, light-red, light-teal, light-warm-gray, light-yellow'.split(', ')
    return random.choice(colors)

def get_or_create_tag(tag_name: str) -> str:
    # Check if the tag exists
    tags = tags_api_instance.get_tags_for_workspace(WORKSPACE_GID, {'opt_fields': 'name'})
    try:
        for tag in tags:
            if tag['name'] == tag_name:
                return tag['gid']
    except ApiException as e:
        # Tag does not exist
        print(f"Exception when calling TagsApi->get_tags_for_workspace: {e}")
    
    # Create the tag if it does not exist
    tag_data = {
        "data": {
            "name": tag_name,
            "workspace": WORKSPACE_GID,
            "color": random_color()
        }
    }
    tag = tags_api_instance.create_tag(tag_data, {'opt_fields': 'gid'})
    return tag['gid']

def schedule(task_name: str, script: str, depends_on: List[str], tags: List[str] = [], title: str = None):
    # Construct the notes with script and dependencies
    notes = f"# Script\n{script}\n\n# Depends on\n"
    for dependency in depends_on:
        dependency_gid = job_name_to_gid.get(dependency, None)
        if dependency_gid:
            notes += f"- {dependency} (https://app.asana.com/0/{WORKSPACE_GID}/{dependency_gid})\n"
        else:
            notes += f"- {dependency} (GID not found)\n"

    # Create the task in the Asana project
    task_data = {
        "data": {
            "name": title or task_name,
            "notes": notes,
            "projects": [PROJECT_GID],
            "memberships": [{"project": PROJECT_GID, "section": BACKLOG_COLUMN_GID}]
        }
    }
    task = tasks_api_instance.create_task(task_data, opts)
    task_gid = task['gid']

    # Add tags to the task
    for tag_name in tags:
        tag_gid = get_or_create_tag(tag_name)
        if tag_gid:
            try:
                tasks_api_instance.add_tag_for_task({"data": {"tag": tag_gid}}, task_gid)
            except ApiException as e:
                print(f"Exception when adding tag '{tag_name}' to task: {e}")

    print(f"Task '{task_name}' created with GID: {task_gid}")
    job_name_to_gid[task_name] = int(task_gid)
    return task_gid


def main(file_path: str):
    config = load_yaml(file_path)
    
    script_template = config['script']
    default_context = config['default']
    stages = config['stages']
    
    jobs_context = {}
    jobs_dependencies = {}

    for stage in stages:
        job_name = stage['name']
        context = {**default_context, **stage}
        
        for nested_level in range(4):
            for key, value in context.items():
                if isinstance(value, str):
                    context[key] = substitute_variables(value, context)
                    context[key] = resolve_dependencies(context[key], jobs_context)
        
        jobs_context[job_name] = context
        jobs_dependencies[job_name] = [match.group(1) for match in re.finditer(r'\$\((\w+)\.\w+\)', str(stage))]

        script = substitute_variables(script_template, context)
        tags = [i.strip() for i in context.get('tags', '').split(',')]
        schedule(job_name, script, jobs_dependencies[job_name], tags=tags, title= context.get('model_id'))


if __name__ == "__main__":
    fire.Fire(main)
