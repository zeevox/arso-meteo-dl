#!/usr/bin/python3

import calendar
import json
import os.path
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from multiprocessing import Manager
from multiprocessing.managers import DictProxy
from typing import Any

import json5
import pandas as pd
import requests_cache
from tqdm import tqdm
from tqdm.contrib.concurrent import process_map

"""
Example requests from which the API was reverse-engineered

https://meteo.arso.gov.si/webmet/archive/data.xml?lang=si&vars=136,137,138,140,141,143,152,144,145,146,147,148,150,149,139,153,151&group=monthlyData1&type=monthly&id=1639&d1=2001-01-01&d2=2001-01-31

https://meteo.arso.gov.si/webmet/archive/locations.xml?d1=1995-06-01&d2=1995-06-30&type=3,2,1&lang=si
"""

session = requests_cache.CachedSession(
    "cache",
    cache_control=False,
    expire_after=timedelta(days=1),
)


class Parameters:
    vars: pd.DataFrame

    def _load_vars(self):
        self.vars = pd.read_csv("vars.csv")

    def __init__(self) -> None:
        self._load_vars()

    def get_var_ids(self) -> tuple[int]:
        return tuple(self.vars["pid"].to_list())

    def _get_mapping(self, map_from="name", map_to="en") -> dict:
        return {
            self.vars.loc[index][map_from]: self.vars.loc[index][map_to]
            for index in self.vars.loc[self.vars[map_to].notnull()].index
        }

    def get_weatherbox_mapping(self) -> dict[str, str]:
        return self._get_mapping("name", "weatherbox")

    def get_aggregation_mapping(self) -> dict[str, str]:
        return self._get_mapping("name", "aggregation")

    def get_name_mapping(self, lang="en") -> dict[str, str]:
        return self._get_mapping("name", lang)


class Locations:
    _all_locations: pd.DataFrame

    def __init__(self) -> None:
        if os.path.isfile("locations_all.txt"):
            with open("locations_all.txt", "r") as locations_all_txt:
                self._all_locations = pd.DataFrame(
                    json.loads(locations_all_txt.read())
                ).transpose()
        else:
            self._download_all_locations()

    def _download_all_locations(self):
        locations_all = {}
        for year_month in tqdm(
            [(year, month) for month in range(1, 13) for year in range(1948, 2023)]
        ):
            print(year_month)
            locations = self._fetch_locations(*year_month)["points"]
            for id, metadata in locations.items():
                if id in locations_all:
                    locations_all[id]["year_months"].append(year_month)
                else:
                    metadata["year_months"] = [year_month]
                    locations_all[id] = metadata
        with open("locations_all.txt", "w") as f:
            f.write(json.dumps(locations_all))

    def _fetch_locations(self, year, month=-1):
        if month != -1:
            d1, d2 = WebMetUtils.get_dates_for_month(year, month)
        else:
            d1, d2 = f"{year}-01-01", f"{year}-12-31"

        params = {"d1": d1, "d2": d2, "type": "1,2,3", "lang": "si"}

        params_str = urllib.parse.urlencode(params, safe=":+,")
        response = session.get(
            f"https://meteo.arso.gov.si/webmet/archive/locations.xml", params=params_str
        )

        if not response.ok:
            return

        return WebMetUtils.pujs_to_json(response.text)

    def get_all_locations(self) -> pd.DataFrame:
        return self._all_locations


class WebMetData:
    def _fetch(
        self,
        vars: list[int],
        group: str,
        type: str,
        station_id: int,
        start_date: str,
        end_date: str,
    ):
        params = {
            "vars": ",".join(map(str, vars)),
            "group": group,
            "type": type,
            "id": station_id,
            "d1": start_date,
            "d2": end_date,
            "lang": "si",
        }

        params_str = urllib.parse.urlencode(params, safe=":+,")
        response = session.get(
            f"https://meteo.arso.gov.si/webmet/archive/data.xml", params=params_str
        )

        return WebMetUtils.pujs_to_json(response.text) if response.ok else None

    parameters = Parameters()

    def fetch_data_for_month(
        self, station_id: int, year_month_tuple: tuple[int, int], vars: list[int]
    ) -> dict[str, Any]:
        year, month = year_month_tuple

        start_date, end_date = WebMetUtils.get_dates_for_month(year, month)

        data = self._fetch(
            vars, "monthlyData1", "monthly", station_id, start_date, end_date
        )

        # remap the dict keys from likes of p29, p30 etc. to more descriptive names e.g. stdni_pad_nad01
        return {
            data["params"][id_key]["name"]: value
            for id_key, value in data["points"][f"_{station_id}"].items()
        }


class WebMetUtils:
    """This class is responsible for some common tasks used in parsing the WebMet API responses"""

    def pujs_to_json(response_str: str) -> dict:
        """This is a slightly hacky way of loading the responses (which are actually JavaScript that is interpreted by the AcademaPUJS) into Python."""
        root = ET.fromstring(response_str)
        data_str = root.text[16:-1]
        # hacky - replace valueless with empty string
        data_str = data_str.replace(":,", ":'',")
        try:
            return json5.loads(data_str)
        except:
            raise ValueError(data_str)

    def get_dates_for_month(year: int, month: int) -> tuple[str, str]:
        """When checking the locations.xml API for stations, it requires a start date and end date, which must be the first and last days of the month. We outsource this job to `calendar`, as I do not want to write code for checking whether a year is a leap year ever again."""
        last_day = calendar.monthrange(year, month)[1]
        return datetime(year, month, 1).strftime("%Y-%m-%d"), datetime(
            year, month, last_day
        ).strftime("%Y-%m-%d")


def get_dl_months_list(station_name):
    """
    Since each weather station has operated for a different amount of time, and there is no API for checking when the station was in operation, we download all the locations.xml responses for months dating back to 1948, which is when the digital archives begin. Then we can check for which of these months' stations lists feature the required station name.

    When downloading the data, we required a list of year-month combinations of when the station was operational, as well as the ID of the station at the time, seeing as the station ID changes when the station is updated or changed in any way.
    """
    station_ids = []
    year_months = []

    all_locations = Locations().get_all_locations()

    df = all_locations.loc[all_locations["name"] == station_name]

    for id in df.index:
        year_months_for_station = df.loc[id]["year_months"]
        year_months.extend(map(tuple, year_months_for_station))
        station_ids.extend([int(id[1:])] * len(year_months_for_station))

    return station_ids, year_months


parameters: Parameters = Parameters()


def mp_get_data_for_station(
    output_dict: dict,
    api: WebMetData,
    vars: list[int],
    station_id: int,
    year_month: tuple[int, int],
) -> None:
    output_dict[year_month] = api.fetch_data_for_month(station_id, year_month, vars)


def download_data_for_station(station_name: str) -> dict:
    data: DictProxy = Manager().dict()

    api: WebMetData = WebMetData()

    station_ids, year_months = get_dl_months_list(station_name)

    get_data_for_station = partial(
        mp_get_data_for_station, data, api, parameters.get_var_ids()
    )

    process_map(get_data_for_station, station_ids, year_months)

    # data is a DictProxy, turn into normal dict
    return dict(data)


def clean_station_data(station_data: dict) -> pd.DataFrame:

    # get data into pandas
    df = pd.DataFrame(station_data).transpose()

    # sort by date, as multiprocessing means order not preserved
    df.sort_index(inplace=True)

    # split year-month tuple into two-col multi-index
    df["year"], df["month"] = zip(*df.index)
    df = df.set_index(["year", "month"])

    # remove all empty columns and then rows
    df.dropna(inplace=True, axis=1, how="all")
    df.dropna(inplace=True, axis=0, how="all")

    # convert all measurements to numeric values
    df[df.columns] = df[df.columns].apply(pd.to_numeric, errors="coerce")

    return df


@dataclass
class MetStation:
    name: str
    lon: float
    lat: float
    alt: int


def get_station_metadata(station_name: str):

    means = {
        "lon": ["mean"],
        "lat": ["mean"],
        "alt": ["mean"],
    }

    df = Locations().get_all_locations()
    matches = df.loc[df["name"] == station_name]
    station = matches.agg(means)

    return MetStation(
        station_name,
        station["lon"]["mean"],
        station["lat"]["mean"],
        station["alt"]["mean"],
    )


def export_station_df_to_csv(station_data: pd.DataFrame, filename: str) -> None:
    station_data.to_csv(filename, encoding="utf-8")


def aggregate_station_data(station_data: pd.DataFrame) -> pd.DataFrame:
    agg_func = {
        k: [v]
        for k, v in parameters.get_aggregation_mapping().items()
        if k in station_data.columns
    }
    agg_df = station_data.groupby(["month"]).agg(agg_func)

    # replace all nan with zeros
    agg_df.fillna(0, inplace=True)

    return agg_df.round(2)


def print_station_data_to_weatherbox(
    station_data: pd.DataFrame, station_metadata: MetStation
):

    agg_df = aggregate_station_data(station_data)

    print("{{Weatherbox")
    print(
        f"| location = {station_metadata.name} ({round(station_metadata.alt)}m elev.) [{station_data.index.min()[0]}-{station_data.index.max()[0]}]"
    )
    print(
        f"| source = National Meteorological Service of Slovenia – Archive<ref>{{Cite web |title=meteo.si - Uradna vremenska napoved za Slovenijo - Državna meteorološka služba RS - Državna meteorološka služba |url=https://meteo.arso.gov.si/ |access-date={datetime.now().strftime('%Y-%m-%d')} |website=meteo.arso.gov.si}}</ref>"
    )
    print(
        """| width = auto
| metric first = yes
| single line  = true
| unit rain days = 0.1 mm
| unit snow days = 0.1 mm
| unit precipitation days = 0.1 mm"""
    )

    weatherbox_map = parameters.get_weatherbox_mapping()

    for col, agg in agg_df.columns:
        for month in agg_df.index:
            if col not in weatherbox_map:
                continue
            print(
                f"| {calendar.month_abbr[month]} {weatherbox_map[col]} = {agg_df.loc[month][(col, agg)]}"
            )

    print("}}")


if __name__ == "__main__":
    # input station name here, as it appears on the meteo.si weather station map
    # go to https://meteo.arso.gov.si/met/sl/archive/ and press ARHIV
    # note that Slovenian characters are poorly encoded,
    # e.g. Bohinjska Češnica is recorded as 'BOHINJSKA Ä\x8cEÅ\xa0NJICA'
    station_name = "LENDAVA"

    # get station altitude and location from the locations.xml endpoint
    station_metadata = get_station_metadata(station_name)

    # download all historical data for that location (the station IDs change when the station is updated, which is why we rely on the name)
    data = download_data_for_station(station_name)

    # clean the dictionary of downloaded data into a nice pandas dataframe
    cleaned = clean_station_data(data)

    # print it for our beauty
    print(cleaned)

    # save it to a csv
    export_station_df_to_csv(cleaned, f'{station_name.replace(" ", "-").lower()}.csv')

    # or print the relevant information as wikitext for a Wikipedia weatherbox
    # see https://en.wikipedia.org/wiki/Template:Weather_box for info
    # and https://en.wikipedia.org/wiki/Triglav_Lodge_at_Kredarica for an example
    print_station_data_to_weatherbox(cleaned, station_metadata)
