import os


def campaign_defaults():
    return {
        "domain_id": os.getenv("KEITARO_CAMPAIGN_DOMAIN", "https://in.posdk.xyz/"),
        "group_id": os.getenv("KEITARO_CAMPAIGN_GROUP", "FORTESTS"),
        "traffic_source_id": os.getenv("KEITARO_CAMPAIGN_SOURCE", "FORTESTS"),
    }
