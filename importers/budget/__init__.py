
from beancount.core.number import D
from beancount.ingest import importer
from beancount.core import amount
from beancount.core import flags
from beancount.core import data

from datetime import date
from dateutil.parser import parse

from decimal import Decimal
import csv
import os
import re
import collections
from itertools import groupby
from operator import itemgetter

CSV_HEADER = "Account,Date,Payee,Notes,Category,Amount"
BEAN_DATA_DIR = "/bean/data"
ACCOUNT_MAP = "account_map.csv"

class ActualBudgetImporter(importer.ImporterProtocol):
    def __init__(self, currency='AUD', file_encoding='utf-8'):
        self.currency = currency
        self.file_encoding = file_encoding

    def identify(self, file_):
        with open(file_.name, encoding=self.file_encoding) as f:
            header = f.readline().strip()
        
        return re.match(header, CSV_HEADER)

    def get_account_map(self):
        # Get account mapping for Budget accounts --> Ledger accounts
        
        found_csv = os.path.exists(ACCOUNT_MAP)
        csv_path = BEAN_DATA_DIR + "/" if not found_csv else ""
        with open(csv_path + ACCOUNT_MAP) as f:
            reader = csv.reader(f)
            account_map = {rows[0]:rows[1] for rows in reader}
        return account_map

    def extract(self, f):
        entries = []

        account_map = self.get_account_map()

        with open(f.name, mode='r') as f:
            rows = []
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)

        # Blank categories are assumed to be transfers
        transfers = []
        for idx, row in enumerate(rows):
            if not row['Category']:
                row['Notes'] = row['Payee']
                row['Payee'] = "Transfer"
            
            row["Abs"] = abs(D(row["Amount"]))

            parse_notes = row["Notes"].split(" #", 1)
            row["Notes"] = parse_notes[0]
            row["Tags"] = ""

            if len(parse_notes) > 1:
                tags = parse_notes[1]
                row["Tags"] = tags.replace("#", "").lower()
                row["Tags"] = tuple(row["Tags"].split(", "))

            # Search and replace account and category according to the account map
            for ledger, budget in account_map.items():
                if row["Account"] == budget and row["Account"]:
                    row["Account"] = ledger
                if row["Category"] == budget and row["Category"]:
                    row["Category"] = ledger

        # Group rows for postings
        grouper = itemgetter("Date", "Account", "Payee", "Notes", "Tags")
        rows = sorted(rows, key = grouper)

        for index, (key, value) in enumerate(groupby(rows, key = grouper)):
            if key[2] != "Transfer":
                meta = data.new_metadata(f.name, index)

                txn = data.Transaction(
                    meta=meta,
                    date=parse(key[0]).date(),
                    flag=flags.FLAG_OKAY,
                    payee=key[2],
                    narration=key[3],
                    tags=set(filter(None, key[4])),
                    links=set(),
                    postings=[],
                )

                total = 0
                for t in value:
                    txn.postings.append(
                        data.Posting(t["Category"], amount.Amount(D(t["Amount"])*-1,
                            "AUD"), None, None, None, None)
                    )

                    total += D(t["Amount"])

                txn.postings.insert(0,
                    data.Posting(key[1], amount.Amount(total,
                        "AUD"), None, None, None, None)
                )

                entries.append(txn)

        grouper = itemgetter("Date", "Payee", "Abs")
        transfers = sorted(rows, key = grouper)

        for index, (key, value) in enumerate(groupby(transfers, key = grouper)):
            if key[1] == "Transfer":
                meta = data.new_metadata(f.name, index)

                txn = data.Transaction(
                    meta=meta,
                    date=parse(key[0]).date(),
                    flag=flags.FLAG_OKAY,
                    payee=key[1],
                    narration="",
                    tags=set(),
                    links=set(),
                    postings=[],
                )

                for t in value:
                    position = 0 if D(t["Amount"]) < 0 else 1
                    txn.postings.insert(position,
                        data.Posting(t["Account"], amount.Amount(D(t["Amount"]),
                            "AUD"), None, None, None, None)
                    )

                entries.append(txn)


        return entries