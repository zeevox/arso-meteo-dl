# Meteo.si (ARSO) climate data downloader

The Slovenian Environment Agency (Slovenian: *Agencija Republike Slovenije za okolje* or *ARSO*) provides digital access to all weather observations and measurements from all of its stations since 1955 (and some from 1948). 

The web interface for retrieving these data is available [here](https://meteo.arso.gov.si/met/sl/archive/). However, this interface is clunky and does not allow for bulk export. The API used by the web UI was reverse engineered to fetch all climatic data for a given station.

Furthermore, methods are provided for aggregating the data and presenting it as a [Wikipedia Weatherbox](https://en.wikipedia.org/wiki/Template:Weather_box), for example the one added to the Wikipedia page for [the weather station at Kredarica](https://en.wikipedia.org/wiki/Triglav_Lodge_at_Kredarica#Weather_station) was generated using this program.

![Wikipedia Weatherbox for the weather station at Kredarica](https://github.com/ZeevoX/arso-meteo-dl/raw/master/images/weatherbox-kredarica.png)

## Updating the list of stations

The `locations_all.txt` file stores data for all stations for which data is available in the ARSO digital archives. It includes metadata about the station as well as the months that the station was operational. This file has been included in the git repository, as fetching all this data requires a non-negligible amount of time depending on your internet connection, and in many use cases the most recents months' data may not be necessary. Should you want the latest available station data, simply delete the `locations_all.txt` file and the list of weather stations will be redownloaded from the meteo.si website the next time that the program is run.

## Dealing with Slovene characters

This will require a small amount of manual work. Go to the meteo.si archive page by clicking the *arhiv* button on [this page](https://meteo.arso.gov.si/met/sl/archive/). Under *Izberi tip podatkov*, select *Mesečni podatki*, then press the calendar icon and select some month. Under *Izberi tip postaj* you can select all the boxes. Now press "Poizvedi". 

Find your desired weather station on the map, note its color. Select *vse meteorološke statistike*, then check any of the boxes that are labelled with the color of your desired weather station. Now press `CTRL`+`SHIFT`+`I` (or whatever equivalent opens the developer tools of your browser, go to the network tab, filter with query `xml`). Switch to the *Postaje* tab and select your weather station. Observe for the network request that is made to `https://meteo.arso.gov.si/webmet/archive/data.xml`. Make a note of the value of the `id=XXX` parameter in the URL.

Now in `ipython`
```python
import fetcher
locations = fetcher.Locations().get_all_locations()
# replace XXX with the ID of your weather station, do not remove the underscore
locations.loc['_XXX'].loc['name']
```
Running the lines one-by-one, in order, will output the poorly encoded name of the weather station. For example, if we wanted data for Portorož Letališče, and followeded  these steps to obtain ID 1896, the output would be `'PORTOROÅ½ - LETALIÅ\xa0Ä\x8cE'`.
