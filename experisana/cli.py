import fire
from experisana.schedule import main as schedule_main
from experisana.worker import main as worker_main
from experisana.autoscale import autoscale


def main():
    fire.Fire({
        'schedule': schedule_main,
        'worker': worker_main,
        'autoscale': autoscale,
    })
    

if __name__ == "__main__":
    main()