import sys
import math
from collections import defaultdict
from pyspark import SparkConf, SparkContext
from elasticsearch import Elasticsearch
import findspark

# Initialize Spark
findspark.init()

# Constants and Configuration for the application
TOPIC = b'yelp-stream'
DTYPE = "restaurant"
YELP_INDEX = "yelpreco"
CONF_PARAM = f"{YELP_INDEX}/{DTYPE}"
USER_LOCATION = (36.1027496, -115.1686673)  # User's location for distance calculation
RADIUS_KM = 6371  # Earth radius in kilometers, used in distance calculation
MAX_DISTANCE = 5  # Maximum distance (in km) for restaurant recommendation
TOP_N = 5  # Number of top restaurants to recommend


def create_es_index():
    """Initializes and creates an Elasticsearch index for storing Yelp data."""
    with Elasticsearch(["http://localhost:9200"]) as es:
        # Check if the index exists, if not, create it
        if not es.indices.exists(index=YELP_INDEX):
            es.indices.create(YELP_INDEX)
            # Define data mapping for Elasticsearch index
            mapping = {
                DTYPE: {
                    "properties": {
                        "businessId": {"type": "string"},
                        "name": {"type": "string"},
                        "full_address": {"type": "string"},
                        "categories": {"type": "string"},
                        "stars": {"type": "string"},
                        "location": {"type": "geo_point", "index": "not_analyzed"},
                    }
                }
            }
            # Apply the mapping to the index
            es.indices.put_mapping(index=YELP_INDEX, doc_type=DTYPE, body=mapping)


def read_elastic_search(sc):
    """Fetches data from Elasticsearch into Spark RDD."""
    # This function returns an RDD created from Elasticsearch data
    return sc.newAPIHadoopRDD(
        inputFormatClass="org.elasticsearch.hadoop.mr.EsInputFormat",
        keyClass="org.apache.hadoop.io.NullWritable",
        valueClass="org.elasticsearch.hadoop.mr.LinkedMapWritable",
        conf={"es.resource": "yelpraw/restaurant"})


def is_relevant_location(yelpData, category_user):
    """Checks if a restaurant's location and category match the user's preferences."""
    if yelpData:
        _, rcvd_data = yelpData
        categories = str(rcvd_data.get("categories")).strip('[]')
        # Checks if the category matches and if the distance is within the specified range
        if category_user in categories or not category_user:
            destination = (float(rcvd_data.get("latitude")), float(rcvd_data.get("longitude")))
            return distance(USER_LOCATION, destination) < MAX_DISTANCE
    return False


def remap_for_elasticsearch(rec):
    """Prepares and remaps data for storage in Elasticsearch."""
    if rec:
        _, data = rec
        # Format location data for geo-point type in Elasticsearch
        location = f"{data['latitude']},{data['longitude']}"
        return ('key', {"businessId": data["business_id"], "name": data["name"],
                        "full_address": data["full_address"], "categories": data["categories"],
                        "stars": data["stars"], "location": location})


def print_results(sorted_data):
    """Prints the top N recommended restaurants."""
    for _, rec in sorted_data:
        result = ' '.join((rec["name"], rec["full_address"], rec["stars"]))
        print(result)


def copy_unique_data(sorted_data, count):
    """Selects unique data up to a specified number."""
    key_dict = defaultdict()
    # Ensures that only unique restaurants are considered up to the specified count
    return [item for item in sorted_data if
            key_dict.setdefault(item[1]["business_id"], "present") == "present" and len(key_dict) <= count]


def main(category_user):
    """Main function to orchestrate the data processing and recommendation logic."""
    # Configures Spark context
    conf = SparkConf().setMaster("local[2]").setAppName("YelpRecommender")
    with SparkContext(conf=conf) as sc:
        rdd_data = read_elastic_search(sc)
        # Filters and sorts data based on user preferences and star ratings
        filtered_data = rdd_data.filter(lambda data: is_relevant_location(data, category_user))
        sorted_data = filtered_data.top(150, key=lambda a: a[1]["stars"])
        topn_data = copy_unique_data(sorted_data, TOP_N)
        print_results(topn_data)
        # Saves the top N data to Elasticsearch
        sorted_rdd = sc.parallelize(topn_data)
        es_data = sorted_rdd.map(remap_for_elasticsearch)
        es_data.saveAsNewAPIHadoopFile(path='-',
                                       outputFormatClass="org.elasticsearch.hadoop.mr.EsOutputFormat",
                                       keyClass="org.apache.hadoop.io.NullWritable",
                                       valueClass="org.elasticsearch.hadoop.mr.LinkedMapWritable",
                                       conf={"es.resource": f"{YELP_INDEX}/{DTYPE}"})


def distance(origin, destination):
    """Calculates the Haversine distance between two points."""
    # Converts lat/long from decimal degrees to radians
    lat1, lon1 = origin
    lat2, lon2 = destination
    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)

    # Haversine formula to calculate the distance
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return RADIUS_KM * c


if __name__ == '__main__':
    # Main entry point of the script
    category_user = sys.argv[1] if len(sys.argv) == 2 else None
    print("Category:", category_user or "No specific category")
    create_es_index()
    main(category_user)
