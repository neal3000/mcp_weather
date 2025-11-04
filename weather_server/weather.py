import httpx
from typing import Optional, Dict, Any
from datetime import datetime

class WeatherService:
    def __init__(self):
        self.base_url = "https://api.open-meteo.com/v1"
    
    async def get_current_weather(self, latitude: float, longitude: float) -> Optional[Dict[str, Any]]:
        """Get current weather data for coordinates"""
        try:
            url = f"{self.base_url}/forecast"
            params = {
                'latitude': latitude,
                'longitude': longitude,
                'current': 'temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,rain,showers,snowfall,weather_code,cloud_cover,pressure_msl,surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m',
                'timezone': 'auto',
                'forecast_days': 1
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                return self._format_current_weather(data)
                
        except Exception as e:
            print(f"Error getting current weather: {e}")
            return None
    
    async def get_forecast(self, latitude: float, longitude: float, days: int = 3) -> Optional[Dict[str, Any]]:
        """Get weather forecast for coordinates"""
        try:
            url = f"{self.base_url}/forecast"
            params = {
                'latitude': latitude,
                'longitude': longitude,
                'daily': 'weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,sunrise,sunset,precipitation_sum,rain_sum,showers_sum,snowfall_sum,precipitation_hours,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant',
                'timezone': 'auto',
                'forecast_days': days
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                return self._format_forecast(data)
                
        except Exception as e:
            print(f"Error getting forecast: {e}")
            return None
    
    def _format_current_weather(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Format current weather data"""
        current = data.get('current', {})
        current_units = data.get('current_units', {})
        
        return {
            'timestamp': current.get('time', ''),
            'temperature': current.get('temperature_2m'),
            'temperature_unit': current_units.get('temperature_2m', '°C'),
            'apparent_temperature': current.get('apparent_temperature'),
            'relative_humidity': current.get('relative_humidity_2m'),
            'humidity_unit': current_units.get('relative_humidity_2m', '%'),
            'weather_code': current.get('weather_code'),
            'weather_description': self._get_weather_description(current.get('weather_code')),
            'is_day': current.get('is_day'),
            'precipitation': current.get('precipitation'),
            'precipitation_unit': current_units.get('precipitation', 'mm'),
            'rain': current.get('rain'),
            'snowfall': current.get('snowfall'),
            'cloud_cover': current.get('cloud_cover'),
            'cloud_cover_unit': current_units.get('cloud_cover', '%'),
            'pressure': current.get('pressure_msl'),
            'pressure_unit': current_units.get('pressure_msl', 'hPa'),
            'wind_speed': current.get('wind_speed_10m'),
            'wind_speed_unit': current_units.get('wind_speed_10m', 'km/h'),
            'wind_direction': current.get('wind_direction_10m'),
            'wind_gusts': current.get('wind_gusts_10m'),
            'location': {
                'latitude': data.get('latitude'),
                'longitude': data.get('longitude'),
                'timezone': data.get('timezone')
            }
        }
    
    def _format_forecast(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Format forecast data"""
        daily = data.get('daily', {})
        daily_units = data.get('daily_units', {})
        
        forecast_days = []
        for i in range(len(daily.get('time', []))):
            day_data = {
                'date': daily['time'][i],
                'weather_code': daily['weather_code'][i],
                'weather_description': self._get_weather_description(daily['weather_code'][i]),
                'temperature_max': daily['temperature_2m_max'][i],
                'temperature_min': daily['temperature_2m_min'][i],
                'temperature_unit': daily_units.get('temperature_2m_max', '°C'),
                'apparent_temperature_max': daily['apparent_temperature_max'][i],
                'apparent_temperature_min': daily['apparent_temperature_min'][i],
                'sunrise': daily['sunrise'][i],
                'sunset': daily['sunset'][i],
                'precipitation_sum': daily['precipitation_sum'][i],
                'precipitation_unit': daily_units.get('precipitation_sum', 'mm'),
                'precipitation_probability': daily.get('precipitation_probability_max', [])[i] if daily.get('precipitation_probability_max') else None,
                'wind_speed_max': daily['wind_speed_10m_max'][i],
                'wind_speed_unit': daily_units.get('wind_speed_10m_max', 'km/h'),
                'wind_gusts_max': daily['wind_gusts_10m_max'][i]
            }
            forecast_days.append(day_data)
        
        return {
            'location': {
                'latitude': data.get('latitude'),
                'longitude': data.get('longitude'),
                'timezone': data.get('timezone')
            },
            'forecast': forecast_days
        }
    
    def _get_weather_description(self, code: int) -> str:
        """Convert weather code to human-readable description"""
        weather_codes = {
            0: "Clear sky",
            1: "Mainly clear", 
            2: "Partly cloudy",
            3: "Overcast",
            45: "Fog",
            48: "Depositing rime fog",
            51: "Light drizzle",
            53: "Moderate drizzle",
            55: "Dense drizzle",
            56: "Light freezing drizzle",
            57: "Dense freezing drizzle",
            61: "Slight rain",
            63: "Moderate rain",
            65: "Heavy rain",
            66: "Light freezing rain",
            67: "Heavy freezing rain",
            71: "Slight snow fall",
            73: "Moderate snow fall",
            75: "Heavy snow fall",
            77: "Snow grains",
            80: "Slight rain showers",
            81: "Moderate rain showers",
            82: "Violent rain showers",
            85: "Slight snow showers",
            86: "Heavy snow showers",
            95: "Thunderstorm",
            96: "Thunderstorm with slight hail",
            99: "Thunderstorm with heavy hail"
        }
        return weather_codes.get(code, "Unknown")

