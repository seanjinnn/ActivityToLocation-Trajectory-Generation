#!/usr/bin/python
#coding:utf-8
import geopandas as gpd
import skmob
import pandas as pd
import numpy as np
from shapely import wkt
from tqdm import tqdm
import matplotlib.pyplot as plt
import matplotlib as mpl
from collections import defaultdict
import operator
import random
from random import random, uniform, choice
from skmob.utils.plot import plot_gdf
from skmob.measures.evaluation import  common_part_of_commuters
import warnings
import matplotlib.pyplot as plt
from collections import defaultdict
import numpy as np
from tqdm import tqdm
import math
import powerlaw
from math import sqrt, sin, cos, pi, asin, pow, ceil

warnings.filterwarnings('ignore')
# 1.Load grids data
def load_spatial_tessellation(tessellation):
    # relevance: population
    M = 0
    spatial_tessellation = {}
    f = np.array(tessellation)

    for line in f:
        i = int(line[0])
        relevance = int(line[3])
        if relevance == 0:
            relevance += 1
        spatial_tessellation[i] = {'lat': float(line[2]),
                                    'lon': float(line[1]),
                                    'relevance': round(relevance)}

        M += relevance

    return spatial_tessellation, M

def generating_list(tdf, days=30):
    user_location = list()
    location = list()
    for i in range(len(tdf)):
        if i % (days*24) == 0 and i != 0:
            user_location.append(location)
            location = list()
        location.append(tdf[i])
    user_location.append(location)
    return user_location

def earth_distance(lat_lng1, lat_lng2):
    lat1, lng1 = [l*pi/180 for l in lat_lng1]
    lat2, lng2 = [l*pi/180 for l in lat_lng2]
    dlat, dlng = lat1-lat2, lng1-lng2
    ds = 2 * asin(sqrt(sin(dlat/2.0) ** 2 + cos(lat1) * cos(lat2) * sin(dlng/2.0) ** 2))
    return 6371.01 * ds  # spherical earth...

# 2. Compute the origin destination matrix
def radiation_od_matrix(spatial_tessellation, M, alpha=0, beta=1):
    print('Computing origin-destination matrix via radiation model\n')

    ## 参数使用 beta 构建OD-matrx
    n = len(spatial_tessellation)
    od_matrix = np.zeros((n, n))

    for id_i in tqdm(spatial_tessellation):  # original
        lat_i, lng_i, m_i = spatial_tessellation[id_i]['lat'], spatial_tessellation[id_i]['lon'], \
                            spatial_tessellation[id_i]['relevance']

        edges = []
        probs = []

        # compute the normalization factor
        normalization_factor = 1.0 / (1.0 - m_i / M)
        #         normalization_factor = 1.0

        destinations_and_distances = []
        for id_j in spatial_tessellation:  # destination
            if id_j != id_i:
                lat_j, lng_j, d_j = spatial_tessellation[id_j]['lat'], spatial_tessellation[id_j]['lon'], \
                                    spatial_tessellation[id_j]['relevance']
                destinations_and_distances += \
                    [(id_j, earth_distance((lat_i, lng_i), (lat_j, lng_j)))]

        # sort the destinations by distance (from the closest to the farthest)
        destinations_and_distances.sort(key=operator.itemgetter(1))

        sij = 0.0
        for id_j, _ in destinations_and_distances:  # T_{ij} = O_i \\frac{1}{1 - \\frac{m_i}{M}}\\frac{m_i m_j}{(m_i + s_{ij})(m_i + m_j + s_{ij})}.
            m_j = spatial_tessellation[id_j]['relevance']

            if (m_i + sij) * (m_i + sij + m_j) != 0:
                prob_origin_destination = normalization_factor * \
                                          ((m_i + alpha * sij) * m_j) / \
                                          ((m_i + (alpha + beta) * sij) * (m_i + (alpha + beta) * sij + m_j))
            else:
                prob_origin_destination = 0

            sij += m_j
            edges += [[id_i, id_j]]
            probs.append(prob_origin_destination)

        probs = array(probs)

        for i, p_ij in enumerate(probs):
            id_i = edges[i][0]
            id_j = edges[i][1]
            od_matrix[id_i][id_j] = p_ij

        # normalization by row
        sum_odm = np.sum(od_matrix[id_i])  # free constrained
        if sum_odm > 0.0:
            od_matrix[id_i] /= sum_odm  # balanced factor

    return od_matrix

# 3. Act2Loc Model
def weighted_random_selection(weights):
    return np.searchsorted(np.cumsum(weights)[:-1], random())

class Act2Loc:
    def __init__(self):
        self.trajectory = []
        self.location2visits = defaultdict(int)
        self.other = defaultdict(int)

    def move(self, home=None, work=None, diary_mobility=None, spatial_tessellation=None,
             od_matrix=None, location_set=None, walk_nums=0):
        self.od_matrix = od_matrix
        self.diary_mobility = np.array(diary_mobility)
        self.home = home
        self.location_set = location_set

        if "W" in set(self.diary_mobility):
            self.work = work

            self.od_matrix[self.home][self.work] = 0  # home -> work
            sum_odm = np.sum(od_matrix[self.home])
            if sum_odm > 0.0:
                self.od_matrix[self.home] /= sum_odm

            self.od_matrix[self.work][self.home] = 0  # work -> home
            sum_odm = np.sum(od_matrix[self.work])
            if sum_odm > 0.0:
                self.od_matrix[self.work] /= sum_odm

        i = 0
        while i < len(self.diary_mobility):
            if self.diary_mobility[i] == 'H':
                next_location = self.home
            elif self.diary_mobility[i] == 'W':
                next_location = self.work
            else:
                if len(self.other) == 0 or self.diary_mobility[i] not in self.other.keys():
                    next_location = self.choose_location()
                    self.other[self.diary_mobility[i]] = next_location
                else:
                    next_location = self.other[self.diary_mobility[i]]

            # Waiting
            self.trajectory.append(next_location)
            i += 1

        # *******************Number the home, work and other labels with grid id.*********************
        cnt = 0
        self.mobility = []
        for i in self.diary_mobility:
            if i == 'H':
                location = self.home
            elif i == 'W':
                location = self.work
            else:
                location = self.other[i]
            self.mobility.append(location)

        return self.mobility

def func(diary_mobility):
    # The number of unique place of a trajectory
    other_set = set()
    for row in range(len(diary_mobility)):
        if diary_mobility[row] != 'H' and diary_mobility[row] != 'W':
            other_set.add(diary_mobility[row])
    other_set = sorted(other_set)

    walk_nums = 0
    if (len(other_set) > 0):
        walk_nums = max(other_set)

    work = None
    location_set = None

    od_matrix = other_matrix
    if 'W' in diary_mobility:
        work = weighted_random_selection(work_matrix[home])

    home_list.append(home)
    work_list.append(work)
    trajectory = []
    trajectory = trajectory_generator.move(home=home, work=work, diary_mobility=diary_mobility, od_matrix=od_matrix,
                                           location_set=location_set, walk_nums=walk_nums)

    return trajectory

if __name__ == '__main__':
    # 1.load the spatial tessellation
    tessellation = gpd.read_file(r'data\1km-grids\sz_1km.shp')
    spatial_tessellation, M = load_spatial_tessellation(tessellation)

    # 2.Compute probability matrix
    work_matrix = radiation_od_matrix(spatial_tessellation, M, alpha=0.13, beta=0.61)
    other_matrix = radiation_od_matrix(spatial_tessellation, M, alpha=0.01, beta=0.45)


    # 3.Load activity type sequences(The whole dataset is not disclosed due to the privacy issue.)
    activity = []
    with open("activity.pkl", "rb") as f:
        activity_set = pickle.load(f)

    # 4. Choose the number of individuals and their Home
    individuals = 1000
    tessellation["pop"] = tessellation["pop"] / (tessellation["pop"].sum() / individuals)
    tessellation["pop"] = tessellation["pop"].astype("int")
    home_df = tessellation[["tile_ID", "pop"]]

    # 5. Trajectory Generation
    trajectory_generator = Act2Loc()
    synthetic_trajectory = []
    home_list = []
    work_list = []
    for i in tqdm(range(len(home_df))):
        home = home_df.iloc[i]["tile_ID"]
        flow = home_df.iloc[i]["pop"]

        diary_mobilitys = []
        for item in range(flow):
            diary_mobility = choice(activity)
            diary_mobilitys.append(diary_mobility)
        synthetic_trajectory.extend(list(map(func, diary_mobilitys)))



