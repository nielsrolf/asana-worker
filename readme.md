# Asana task worker and scheduler

Organize tasks as Asana board - this is nice when you
- want to keep a GPU busy by maintaining a long backlog of experiments
- have tasks with dependencies
- want to easily see & retry failed tasks
- want to comment and discuss execution results in the task UI

<img src="img/columns.png" width="420" alt="Detail view">

**How does it work?**
- save your credentials and asana board id in `.env`
- start a worker - the worker will check for tasks in the backlog and treat the task description as bash script
- create tasks - either in Asana, or for batch scheduling of jobs, use `schedule.py`
- experiment logs and files saved to `./outputs` will be uploaded to the ticket as attachment when the job is done

## Install
Clone the repo and run
```
pip install -e .
```

## Start a worker
```
experisana worker
```

## Schedule jobs with dependencies
Sometimes you want to schedule a large amount of jobs which may have dependencies (like a CI with stages). You can do this with:
```
experisana schedule example.yaml
```

## Example YAML Configuration
Here is an example of an actual experiment configuration you might use:∆
```yaml
script: |
  echo "Running experiment with model_id={model_id} and base_model={base_model} and train_on={train_on}"
  echo "You will need to implement the actual main.py yourself - this is just an example!"
  python main.py --config-name template-{cot} \
    model_id={model_id} \
    training.base_model={base_model} \
    training.train_dataset={train_on}

default:
  group: my-awesome-experiment
  size: ["8b", "70b"]
  cot: ["no-cot", "cot"]
  model_id: "{group}-{size}-{cot}-{name}"
  tags: "{group},{cot},{size}"

  eval-dataset: path/to/some/data.jsonl
  helpfulness: path/to/training/v1.jsonl
  harmlessness: path/to/training/v2.jsonl
  hhh: path/to/training/v3.jsonl

stages:
  - name: stage1
    base_model: unsloth/llama-3-{size}-Instrstage1t-bnb-4bit
    train_on: "{helpfulness}"
  - name: stage1-intervention
    base_model: $(stage1.model_id)
    train_on: "{harmlessness}"
  - name: stage1-control
    base_model: $(stage1.model_id)
    train_on: "{hhh}"
```

### Explanation
- Default Section: Defines default values for parameters. Lists indicate a sweep will be performed over those values.
- Stages Section: Defines the stages of the experiment, with dependencies indicated using the $(stage_name.parameter) syntax.
When running schedule.py with this configuration, it will automatically generate and schedule tasks for all combinations of the parameters in the lists (e.g., for all combinations of size and cot).

## Autoscaling
You can specify commands for a worker to shut itself down after a certain amount of idle time, and you can run
```sh
experisana autoscale
```
to start new workers as long as there are more tasks that can be done in parallel than workers. For this, you need a `experisana.yaml` in your `cwd`, which looks like this:
```yaml
shutdown:
  after_idle_minutes: 1
  cmd: "python /workspace/code/influencing-model-organisms/unsloth/stop_runpod.py {worker_id}" # This runs on the worker

scale:
  max: 2
  cmd: "python start_runpod.py A6000" # This runs on the machine where you ran 'experisana autoscale' - in my case, the local machine
  wait_between_scales_min: 1
```