"""gpt-researcher functions captured before the worker patches them.

The worker routes page reading through the search part by replacing Scraper.extract_data_from_url,
while search itself still needs the original to fetch pages. Importing this module first keeps the
untouched function whichever part loads first in a process.
"""

from gpt_researcher.scraper.scraper import Scraper

ORIGINAL_EXTRACT = Scraper.extract_data_from_url
