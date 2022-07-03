
from beancount.core.number import D
from beancount.ingest import importer
from beancount.core import amount
from beancount.core import flags
from beancount.core import data

import csv
import os
import re
from dateutil.parser import parse
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
        # CSV should contain three columns "Budget Account, Ledger Account, Off-Budget"
        # 1nd Column (Budget Acount) will be the key
        try:
            found_csv = os.path.exists(ACCOUNT_MAP)
            csv_path = BEAN_DATA_DIR + "/" if not found_csv else ""
            with open(csv_path + ACCOUNT_MAP) as f:
                reader = csv.reader(f)
                account_map = {rows[0]:{'Ledger Account': rows[1], 'Off-Budget': rows[2]} for rows in reader}
            return account_map
        except:
            return False

    def get_ledger_account(self, account):
        try:
            account_map = self.get_account_map()
            return account_map[account]["Ledger Account"] if account_map else account
        except KeyError:
            return account

    def is_off_budget(self, account):
        try:
            if account_map:
                account_map = self.get_account_map()
                if account_map[account]["Off-Budget"] == "Y":
                    return True
            return account
        except KeyError:
            return False

    def is_bs_account(self, account):
        try:
            account_map = self.get_account_map()
            if account_map:
                ledger_account = account_map[account]["Ledger Account"]
                account_type = ledger_account.split(":")[0]
                if account_type in ("Assets","Liabilities"):
                    return True
            return account
        except KeyError:
            return False

    def extract(self, f):
        # Store csv rows in dict
        with open(f.name, mode='r') as f:
            rows = []
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)

        # Clean up data
        for index, row in enumerate(rows):
            # Change accounts based on account mapping details
            row["Account"] = self.get_ledger_account(row["Account"])
            row["Category"] = self.get_ledger_account(row["Category"])

            # Create key with absolute values
            row["Abs"] = abs(D(row["Amount"]))

            # Create is_transfer key
            row['Transfer'] = False

            # Parse notes for tags
            parse_notes = row["Notes"].split("#", 1)
            row["Notes"] = parse_notes[0].strip()
            row["Tags"] = ""
            if len(parse_notes) > 1:
                tags = parse_notes[1]
                row["Tags"] = tags.replace("#", "").lower()
                row["Tags"] = tuple(row["Tags"].split(", "))

            # If payee is a balance sheet account and there is no cateogry then assume it to be a transfer
            if self.is_bs_account(row['Payee']) and not row['Category']:
                if not row['Notes']:
                    row['Transfer'] = True
                    row["Payee"] = self.get_ledger_account(row["Payee"])

                if row['Notes']:
                    row['Category'] = self.get_ledger_account(row["Payee"])
                    row['Payee'] = ""

            if not row['Category']:
                row['Category'] = self.get_ledger_account("No Category")

        # Group rows for postings if the specified columns match
        trans_grouper = itemgetter("Date", "Transfer", "Account", "Payee", "Notes", "Tags")
        trans_sort = sorted(rows, key = trans_grouper)
        trans_list = [
            {key: list(values)} 
            for key, values in groupby(trans_sort, key = trans_grouper) 
            if not key[1] and key[3] != 'Starting Balance'
            ]

        # Create entries
        # Create transaction entries
        entries = []
        for dict in trans_list:
            for index, (key, values) in enumerate(dict.items()):
                parsed_date = parse(key[0], dayfirst = True).date()
                trans_payee = key[3]
                trans_narration = key[4]
                trans_tags = key[5]

                meta = data.new_metadata(f.name, index, {"effective-date": parsed_date})

                txn = data.Transaction(
                    meta=meta,
                    date=parsed_date,
                    flag=flags.FLAG_OKAY,
                    payee=trans_payee,
                    narration=trans_narration,
                    tags=set(filter(None, trans_tags)),
                    links=set(),
                    postings=[],
                )

                total = 0
                for value in values:
                    txn.postings.append(
                        data.Posting(value["Category"], amount.Amount(D(value["Amount"])*-1,
                            "AUD"), None, None, None, None)
                    )
                    total += D(value["Amount"])

                txn.postings.insert(0,
                    data.Posting(key[2], amount.Amount(total,
                        self.currency), None, None, None, None)
                )

                entries.append(txn)

        tfr_grouper = itemgetter("Date", "Transfer", "Abs")
        tfr_sort = sorted(rows, key = tfr_grouper)
        tfr_list = [
            {key: list(values)} 
            for key, values in groupby(tfr_sort, key = tfr_grouper) 
            if key[1]
            ]

        # Create transfer entries
        for dict in tfr_list:
            for index, (key, values) in enumerate(dict.items()):
                parsed_date = parse(key[0], dayfirst = True).date()

                meta = data.new_metadata(f.name, index, {"effective-date": parsed_date})

                txn = data.Transaction(
                    meta=meta,
                    date=parsed_date,
                    flag=flags.FLAG_OKAY,
                    payee=None,
                    narration="Transfer",
                    tags=set(),
                    links=set(),
                    postings=[],
                )

                total = 0
                for value in values:
                    position = 0 if D(value["Amount"]) < 0 else 1
                    txn.postings.insert(position,
                        data.Posting(value["Account"], amount.Amount(D(value["Amount"]),
                            self.currency), None, None, None, None)
                    )
                    total += D(value["Amount"])
                    to_account = value["Payee"]

                # Complete transfer journal using the account specified in the Notes if journal doesn't add up to 0
                # This will happen if you only export for a single account instead of all accounts
                x = 1 if total < 0 else 0
                if total != D(0):
                    txn.postings.insert(x,
                        data.Posting(to_account, amount.Amount(-total,
                            self.currency), None, None, None, None)
                    )

                entries.append(txn)

        return entries