# =================================================================
#
# Authors: Louis-Philippe Rousseau-Lambert
#          <louis-philippe.rousseaulambert@ec.gc.ca>
#
# Copyright (c) 2021 Louis-Philippe Rousseau-Lambert
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation
# files (the "Software"), to deal in the Software without
# restriction, including without limitation the rights to use,
# copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following
# conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
# OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.
#
# =================================================================

import tempfile
import logging

import numpy as np
import xarray

from pygeoapi.provider.base import (BaseProvider,
                                    ProviderConnectionError,
                                    ProviderNoDataError,
                                    ProviderQueryError)
from pygeoapi.provider.xarray_ import (read_data,
                                       XarrayProvider,
                                       _convert_float32_to_float64,
                                       _get_zarr_data)

LOGGER = logging.getLogger(__name__)


class CMIP5Provider(XarrayProvider):
    """CMIP5 Provider"""

    def __init__(self, provider_def):
        """
        Initialize object
        :param provider_def: provider definition
        :returns: pygeoapi.provider.xarray_.XarrayProvider
        """

        BaseProvider.__init__(self, provider_def)

        try:
            self._data = open_data(self.data)
            self._coverage_properties = self._get_coverage_properties()

            self.axes = [self._coverage_properties['x_axis_label'],
                         self._coverage_properties['y_axis_label'],
                         self._coverage_properties['time_axis_label']]

            if 'RCP' in self.data:
                self.axes.append('scenario')
            if 'season' in self.data:
                self.axes.append('season')
            if 'avg_20years' not in self.data:
                self.axes.append('percentile')

            self.fields = self._coverage_properties['fields']
        except Exception as err:
            LOGGER.warning(err)
            raise ProviderConnectionError(err)

    def get_coverage_domainset(self):
        """
        Provide coverage domainset

        :returns: CIS JSON object of domainset metadata
        """

        c_props = self._coverage_properties

        domainset = super().get_coverage_domainset(self)

        time_resolution = c_props['restime']['value']
        time_period = c_props['restime']['period']
        domainset['generalGrid']['axis'][2]['uomLabel'] = time_period
        domainset['generalGrid']['axis'][2]['resolution'] = time_resolution

        new_axis_name = []
        new_axis = []

        if 'avg_20years' not in self.data:
            new_axis_name.extend(['percentile'])
            new_axis.extend([{
                             'type': 'IrregularAxis',
                             'axisLabel': 'percentile',
                             'coordinate': [5, 25, 50, 75, 95],
                             'lowerBound': 5,
                             'upperBound': 95,
                             'uomLabel': '%',
                             }])

        if 'RCP' in self.data:
            new_axis.extend([{
                             'type': 'IrregularAxis',
                             'axisLabel': 'scenario',
                             'coordinate': ['RCP2.6', 'RCP4.5', 'RCP8.5']
                             }])
            new_axis_name.append('scenario')

        if 'season' in self.data:
            new_axis.extend([{
                             'type': 'IrregularAxis',
                             'axisLabel': 'season',
                             'coordinate': ['DJF', 'MAM', 'JJA', 'SON']
                             }])
            new_axis_name.append('season')

        domainset['generalGrid']['axisLabels'].extend(new_axis_name)
        domainset['generalGrid']['axis'].extend(new_axis)

        return domainset

    def _get_coverage_properties(self):
        """
        Helper function to normalize coverage properties

        :returns: `dict` of coverage properties
        """

        time_var, y_var, x_var = [None, None, None]
        for coord in self._data.coords:
            if coord.lower() == 'time':
                time_var = coord
                continue
            if self._data.coords[coord].attrs['units'] == 'degrees_north':
                y_var = coord
                continue
            if self._data.coords[coord].attrs['units'] == 'degrees_east':
                x_var = coord
                continue

        if self.x_field is None:
            self.x_field = x_var
        if self.y_field is None:
            self.y_field = y_var
        if self.time_field is None:
            self.time_field = time_var

        properties = {
            'bbox': [
                self._data.coords[self.x_field].values[0],
                self._data.coords[self.y_field].values[0],
                self._data.coords[self.x_field].values[-1],
                self._data.coords[self.y_field].values[-1],
            ],
            'time_range': [
                self._to_datetime_string(
                    self._data.coords[self.time_field].values[0]
                ),
                self._to_datetime_string(
                    self._data.coords[self.time_field].values[-1]
                )
            ],
            'bbox_crs': 'http://www.opengis.net/def/crs/OGC/1.3/CRS84',
            'crs_type': 'GeographicCRS',
            'x_axis_label': self.x_field,
            'y_axis_label': self.y_field,
            'time_axis_label': self.time_field,
            'width': self._data.dims[self.x_field],
            'height': self._data.dims[self.y_field],
            'time': self._data.dims[self.time_field],
            'time_duration': self.get_time_coverage_duration(),
            'bbox_units': 'degrees',
            'resx': np.abs(self._data.coords[self.x_field].values[1]
                           - self._data.coords[self.x_field].values[0]),
            'resy': np.abs(self._data.coords[self.y_field].values[1]
                           - self._data.coords[self.y_field].values[0]),
            'restime': self.get_time_resolution()
        }

        if 'crs' in self._data.variables.keys():
            properties['bbox_crs'] = '{}/{}'.format(
                'http://www.opengis.net/def/crs/OGC/1.3/',
                self._data.crs.epsg_code)

            properties['inverse_flattening'] = self._data.crs.\
                inverse_flattening

            properties['crs_type'] = 'ProjectedCRS'

        properties['axes'] = [
            properties['x_axis_label'],
            properties['y_axis_label'],
            properties['time_axis_label']
        ]

        properties['fields'] = [name for name in self._data.variables
                                if len(self._data.variables[name].shape) >= 3]

        return properties

    def get_time_resolution(self):
        """
        Helper function to derive time resolution
        :returns: time resolution string
        """

        if self._data[self.time_field].size > 1:

            if 'monthly_ens' in self.data:
                period = 'month'
            else:
                period = 'year'

            return {'value': 1, 'period': period}

        else:
            return None

    def _to_datetime_string(self, datetime_):
        """
        Convenience function to formulate string from various datetime objects

        :param datetime_obj: datetime object (native datetime, cftime)

        :returns: str representation of datetime
        """

        try:
            if 'monthly_ens' in self.data:
                month = datetime_.astype('datetime64[M]').astype(int) % 12 + 1
                year = datetime_.astype('datetime64[Y]').astype(int) + 1970
                value = '{}-{}'.format(year, str(month).zfill(2))
            else:
                value = datetime_.astype('datetime64[Y]').astype(int) + 1970
                value = str(value)
            return value
        except Exception as err:
            LOGGER.error(err)

    def query(self, range_subset=['tas'], subsets={},
              bbox=[], datetime_=None, format_='json'):
        """
         Extract data from collection collection

        :param range_subset: list of data variables to return
        :param subsets: dict of subset names with lists of ranges
        :param bbox: bounding box [minx,miny,maxx,maxy]
        :param datetime: temporal (datestamp or extent)
        :param format_: data format of output

        :returns: coverage data as dict of CoverageJSON or native format
        """

        if 'scenario' in subsets:
            scenario = subsets['scenario']
            try:
                if len(scenario) > 1:
                    msg = 'multiple scenario are not supported'
                    LOGGER.error(msg)
                    raise ProviderQueryError(msg)
                elif scenario[0] not in ['RCP2.6', 'hist']:
                    scenario_value = scenario[0].replace('RCP', '')
                    self.data = self.data.replace('2.6', scenario_value)
                    self._data = open_data(self.data)
            except Exception as err:
                LOGGER.error(err)
                raise ProviderQueryError(err)

            subsets.pop('scenario')

        if 'percentile' in subsets:
            percentile = subsets['percentile']

            try:
                if percentile != [50]:
                    pctl = str(percentile[0])
                    self.data = self.data.replace('pctl50',
                                                  'pctl{}'.format(pctl))
                    self._data = open_data(self.data)

            except Exception as err:
                LOGGER.error(err)
                raise ProviderQueryError(err)

            subsets.pop('percentile')

        if 'season' in subsets:
            seasonal = subsets['season']

            try:
                if len(seasonal) > 1:
                    msg = 'multiple seasons are not supported'
                    LOGGER.error(msg)
                    raise ProviderQueryError(msg)
                elif seasonal != ['DJF']:
                    season = str(seasonal[0])
                    self.data = self.data.replace('DJF',
                                                  season)
                    self._data = open_data(self.data)

            except Exception as err:
                LOGGER.error(err)
                raise ProviderQueryError(err)

            subsets.pop('season')

        if not range_subset and not subsets and format_ != 'json':
            LOGGER.debug('No parameters specified, returning native data')
            if format_ == 'zarr':
                return _get_zarr_data(self._data)
            else:
                return read_data(self.data)

        data = self._data[[*range_subset]]

        if any([self._coverage_properties['x_axis_label'] in subsets,
                self._coverage_properties['y_axis_label'] in subsets,
                self._coverage_properties['time_axis_label'] in subsets,
                bbox,
                datetime_ is not None]):

            LOGGER.debug('Creating spatio-temporal subset')

            query_params = {}
            for key, val in subsets.items():
                if data.coords[key].values[0] > data.coords[key].values[-1]:
                    LOGGER.debug('Reversing slicing low/high')
                    query_params[key] = slice(val[1], val[0])
                else:
                    query_params[key] = slice(val[0], val[1])

            if bbox:
                if all([self._coverage_properties['x_axis_label'] in subsets,
                        self._coverage_properties['y_axis_label'] in subsets,
                        len(bbox) > 0]):
                    msg = 'bbox and subsetting by coordinates are exclusive'
                    LOGGER.warning(msg)
                    raise ProviderQueryError(msg)
                else:
                    query_params[self._coverage_properties['x_axis_label']] = \
                        slice(bbox[0], bbox[2])
                    query_params[self._coverage_properties['y_axis_label']] = \
                        slice(bbox[3], bbox[1])

            if datetime_ is not None:
                if self._coverage_properties['time_axis_label'] in subsets:
                    msg = 'datetime and temporal subsetting are exclusive'
                    LOGGER.error(msg)
                    raise ProviderQueryError(msg)
                else:
                    if '/' in datetime_:

                        begin, end = datetime_.split('/')

                        if begin < end:
                            query_params[self.time_field] = slice(begin, end)
                        else:
                            LOGGER.debug('Reversing slicing from high to low')
                            query_params[self.time_field] = slice(end, begin)
                    else:
                        query_params[self.time_field] = datetime_

            LOGGER.debug('Query parameters: {}'.format(query_params))
            try:
                data = data.loc[query_params]
            except Exception as err:
                LOGGER.warning(err)
                raise ProviderQueryError(err)

        if (any([data.coords[self.x_field].size == 0,
                data.coords[self.y_field].size == 0])):
            msg = 'No data found'
            LOGGER.warning(msg)
            raise ProviderNoDataError(msg)

        out_meta = {
            'bbox': [
                data.coords[self.x_field].values[0],
                data.coords[self.y_field].values[0],
                data.coords[self.x_field].values[-1],
                data.coords[self.y_field].values[-1]
            ],
            "time": [
                self._to_datetime_string(
                    data.coords[self.time_field].values[0]),
                self._to_datetime_string(
                    data.coords[self.time_field].values[-1])
            ],
            "driver": "xarray",
            "height": data.dims[self.y_field],
            "width": data.dims[self.x_field],
            "time_steps": data.dims[self.time_field],
            "variables": {var_name: var.attrs
                          for var_name, var in data.variables.items()}
        }

        LOGGER.debug('Serializing data in memory')
        if format_ == 'json':
            LOGGER.debug('Creating output in CoverageJSON')
            return self.gen_covjson(out_meta, data, range_subset)
        elif format_ == 'zarr':
            LOGGER.debug('Returning data in native zarr format')
            return _get_zarr_data(data)
        # elif format_.lower() == 'geotiff':
        #     if len(range_subset) == 1:
        #         import rioxarray
        #         with tempfile.TemporaryFile() as fp:
        #             LOGGER.debug('Returning data in GeoTIFF format')
        #             data.rio.write_crs("epsg:4326", inplace=True)
        #             data[range_subset[0]].rio.to_raster('/tmp/tmp.tif')
        #             with open('/tmp/tmp.tif') as fp:
        #                 fp.seek(0)
        #                 return fp
        #     else:
        #         err = 'Only one range subset supoported for GeoTIFF'
        #         LOGGER.error(err)
        #         raise ProviderQueryError(err)

        else:  # return data in native format
            with tempfile.TemporaryFile() as fp:
                LOGGER.debug('Returning data in native NetCDF format')
                fp.write(data.to_netcdf())
                fp.seek(0)
                return fp.read()


def open_data(data):
    """
    Convenience function to open multiple files with xarray
    :param data: path to files

    :returns: xarray dataset
    """

    try:
        open_func = xarray.open_mfdataset
        _data = open_func(data)
        _data = _convert_float32_to_float64(_data)

        return _data
    except Exception as err:
        LOGGER.error(err)
