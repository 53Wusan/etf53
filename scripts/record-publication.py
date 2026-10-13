"""Persist a receipt only after the Pages deployment has succeeded."""
import argparse

from app.services.account_observation import record_publication

parser = argparse.ArgumentParser()
parser.add_argument("day")
args = parser.parse_args()
record_publication(args.day)
print("Publication receipt saved without rewriting daily observations")
