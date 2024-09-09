import fire
from experisana.schedule import process_yaml as schedule_main
from experisana.worker import main as worker_main
from experisana.autoscale import autoscale
from experisana.pull import pull_attachments

def main():
    fire.Fire({
        'schedule': schedule_main,
        'worker': worker_main,
        'autoscale': autoscale,
        'pull': pull_attachments,
    })

if __name__ == "__main__":
    main()