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
This creates one task for each job that needs to be run plus an additional master task that links to all tasks, to better keep track of experiment bundles and to simplify artifact downloading via `experisana pull`.

## Pull results
When scheduling multiple tasks via the above method, you can download artifacts that are uploaded to the task via the following command:
```
experisana pull --url <url to master task> # Eg: https://app.asana.com/0/123456789/123456789
```
Alternatively, you can pull all tasks with a given tag:
```
experisana pull --tag some-tag
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

sweep:
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
- sweep Section: Defines sweep values for parameters. Lists indicate a sweep will be performed over those values.
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


## Nested Parameters and Advanced Configuration

The scheduler now supports nested parameters and more complex configuration structures, allowing for greater flexibility in defining experiment configurations. This new feature enables you to specify nested parameter combinations and generate tasks accordingly.

### Example Configuration

Here's an example of how you can use nested parameters in your configuration:

```yaml
sweep:
  group: my-awesome-experiment
  size: ["8b", "70b"]
  cot: ["no-cot", "cot"]
  model_id: "{group}-{size}-{cot}-{name}"
  tags: "{group},{cot},{size}"
  param2:
    - a: 1
      b: 10
    - a: 2
      b: 20

script: |
  echo "Running experiment with model_id={model_id} and param2.a={param2.a} and param2.b={param2.b}"
  python main.py --config-name template-{cot} \
    model_id={model_id} \
    training.size={size} \
    training.param2_a={param2.a} \
    training.param2_b={param2.b}

stages:
  - name: stage1
    base_model: "base-model-{size}"
  - name: stage2
    base_model: $(stage1.model_id)
```

In this configuration:

- Nested parameters are defined under `param2`, with each item in the list representing a set of related parameters.
- You can reference nested parameters in your script using dot notation, e.g., `{param2.a}` and `{param2.b}`.
- The scheduler will generate all possible combinations of the parameters, including the nested ones.

### Behavior

1. **Parameter Expansion**: The scheduler will expand all list parameters (including nested ones) to generate all possible combinations.

2. **Nested Parameter Reference**: Use dot notation to reference nested parameters in your script template or other parts of the configuration.

3. **Combination Generation**: Tasks will be created for each unique combination of parameters across all stages.

4. **Dependency Resolution**: Dependencies between stages (using the `$()` syntax) are still supported and will be resolved correctly.

### Usage

To use this feature:

1. Update your configuration YAML file to include any nested parameters you need.
2. Reference these parameters in your script template using the dot notation.
3. Run the scheduler as usual:

```
python schedule.py path/to/your/config.yaml
```

To preview the generated scripts without creating tasks:

```
python schedule.py path/to/your/config.yaml --onlyprint=True
```

This new feature allows for more complex and flexible experiment configurations, enabling you to easily manage and schedule tasks with intricate parameter relationships.
