from config import Config
from manager import Manager


CONFIG = Config.model_validate(
    {
        "managers": [
            {
                "name": "host1",
                "address": "127.0.0.1",
                "token": "22a119cf-0bf3-4fb0-8c13-bd452a03432d",
            }
        ],
        "services": [
            {
                "name": "time",
                "image": "alpine-virt-3.18.6-x86_64.iso",
                "port": 8080,
            }
        ],
        "vms": [
            {
                "service": "time",
                "address": "127.0.0.1",
                "token": "f6f545eb-fa1b-489e-9c32-5b9260c59255",
            }
        ],
    }
)


def main():
    manager = Manager(CONFIG)
    print("manager", manager)


if __name__ == "__main__":
    main()
