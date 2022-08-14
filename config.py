import os, sys

# beancount doesn't run from this directory
sys.path.append(os.path.dirname(__file__))

# importers located in the importers directory
from importers import custom_csv, budget, coinspot

CONFIG = [
     custom_csv.CSVImporter(),
     budget.ActualBudgetImporter(),
     coinspot.CoinSpotImporter()
]