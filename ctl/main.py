from argparse import ArgumentParser, FileType

from pymongo import MongoClient

from manager.config import Config


def main():
    parser = ArgumentParser()
    parser.add_argument(
        "-c",
        "--config",
        type=FileType(),
        default="config.json",
        help="configuration to apply",
    )
    parser.add_argument(
        "-d",
        "--db",
        type=str,
        default="mongodb://localhost/wso",
        help="database connection string",
    )

    args = parser.parse_args()

    config_str = args.config.read()
    new_config = Config.model_validate_json(config_str)

    mongo = MongoClient(args.db)
    db = mongo.get_default_database()
    db.configs.replace_one(
        {"_id": "config"}, new_config.model_dump(mode="json"), upsert=True
    )


if __name__ == "__main__":
    main()
