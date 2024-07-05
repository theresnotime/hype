import logging
import os.path
import schedule
import time
from config import Config
from mastodon import Mastodon


class Hype:
    def __init__(self, config: Config) -> None:
        self.config = config
        logging.basicConfig(
            format="%(asctime)s %(levelname)-8s %(message)s",
            level=logging.getLevelName(self.config.log_level),
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self.log = logging.getLogger("hype")
        self.log.info("Config loaded")

    def login(self):
        self.client = self.init_client(self.config.bot_account.server)
        self.log.info(f"Logging in to {self.config.bot_account.server}")
        self.client.log_in(
            self.config.bot_account.email,
            self.config.bot_account.password,
            to_file=f"secrets/{self.config.bot_account.server}_usercred.secret",
        )

    def update_profile(self):
        self.log.info("Update bot profile")
        subscribed_instances_list = "\n".join(
            [f"- {instance}" for instance in self.config.subscribed_instances]
        )
        profile_suffix = "[Safety]\n- I auto-delete after two weeks\n- I don't boost posts from users who have `nobot` in their bio\n- I don't boost posts from users who are undiscoverable/unindexable"
        note = f"{self.config.profile.strip()}\n\n[Instance list]\n{subscribed_instances_list.strip()}\n\n{profile_suffix}"
        fields = [(key, value) for key, value in self.config.fields.items()]
        self.client.account_update_credentials(
            note=note, bot=True, discoverable=True, fields=fields
        )

    def boost(self):
        self.log.info("Run boost")
        for instance in self.config.subscribed_instances:
            try:
                # Create mastodon API client
                mastodon_client = self.init_client(instance.name)
                # Fetch statuses from every subscribed instance
                trending_statuses = mastodon_client.trending_statuses()[
                    : instance.limit
                ]
                counter = 0
                for trending_status in trending_statuses:
                    counter += 1
                    # Get snowflake-id of status on the instance where the status will be boosted  # noqa: E501
                    status = self.client.search_v2(
                        trending_status["uri"], result_type="statuses"
                    )["statuses"]
                    if len(status) > 0:
                        status = status[0]
                        account_bio = status["account"]["note"]
                        account = status["account"]["acct"]
                        # check if account is a bot, undiscoverable or unindexable
                        if (
                            status["account"]["bot"]
                            or not status["account"]["discoverable"]
                            or not status["account"]["indexable"]
                        ):
                            self.log.warning(
                                f"{instance.name}: {counter}/{len(trending_statuses)} Skipping because {account} is a bot/undiscoverable/unindexable"
                            )
                            continue
                        # check if account has a bio
                        if account_bio is None:
                            self.log.warning(
                                f"{instance.name}: {counter}/{len(trending_statuses)} Skipping because {account} seems to have no bio"
                            )
                            continue
                        # check if account has "nobot" in bio, and skip if it does
                        if "nobot" in account_bio.lower():
                            self.log.warning(
                                f"{instance.name}: {counter}/{len(trending_statuses)} Skipping because of 'nobot' in {account}'s bio"
                            )
                            continue
                        # check if post comes from a filtered instance
                        source_account = account.split("@")
                        server = source_account[1]
                        filtered = server in self.config.filtered_instances
                        # check if attached images have a description/alt text
                        images_described = True
                        for attachment in status.media_attachments:
                            if (
                                attachment.description is None
                                or attachment.description == ""
                            ):
                                images_described = False
                        # Boost if not already boosted
                        already_boosted = status["reblogged"]
                        if not already_boosted and not filtered and images_described:
                            self.client.status_reblog(status)
                            # if delay is set, wait for that amount of minutes
                            if self.config.delay:
                                self.log.info(
                                    f"Sleeping for {self.config.delay} seconds after boosting {status.url}"
                                )
                                time.sleep(self.config.delay)
                        self.log.info(
                            f"{instance.name}: {counter}/{len(trending_statuses)} {'ignore (already boosted/filtered)' if (already_boosted or filtered) else 'boost'}"
                        )
                    else:
                        self.log.warning(
                            f"{instance.name}: {counter}/{len(trending_statuses)} could not find post by id"
                        )
            except Exception as e:
                self.log.error(
                    f"{instance.name}: Could not fetch instance. Sorry. - {e}"
                )

    def start(self):
        self.boost()
        self.log.info(f"Schedule run every {self.config.interval} minutes")
        schedule.every(self.config.interval).minutes.do(self.boost)
        while True:
            schedule.run_pending()
            time.sleep(1)

    def init_client(self, instance_name: str) -> Mastodon:
        secret_path = f"secrets/{instance_name}_clientcred.secret"
        if not os.path.isfile(secret_path):
            self.log.info(f"Initialize client for {instance_name}")
            Mastodon.create_app(
                instance_name,
                api_base_url=f"https://{instance_name}",
                to_file=secret_path,
            )
        else:
            self.log.info(f"Client for {instance_name} is already initialized.")
        return Mastodon(
            client_id=secret_path,
            ratelimit_method="pace",
        )
