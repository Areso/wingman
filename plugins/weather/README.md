# Weather plugin
This plugin is designed to provide Weather forecast  
For now on, it is working for Republic of Cyprus  
The source is openweathermap.org  
  
## Setup
1. create .venv with virtual environment
2. install dependecies from requirements.txt
3. register at openweathermap.org , get an API key
4. create secret file, and put there the API key
5. edit config.toml , write down the full path to the API secret file and name of the secret file itself  

## Smoke test
1. source .venv/bin/activate ; python3 weather.py
