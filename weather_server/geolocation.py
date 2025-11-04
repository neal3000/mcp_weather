import httpx
import ipaddress
from typing import Optional, Tuple, Dict, Any

class GeolocationService:
    def __init__(self):
        self.ipapi_url = "http://ipapi.co/json/"
        self.ifconfig_url = "https://ifconfig.io/json"
    
    async def is_private_ip(self, ip: str) -> bool:
        """Check if IP address is private (RFC 1918)"""
        try:
            ip_obj = ipaddress.ip_address(ip)
            return ip_obj.is_private
        except ValueError:
            return False
    
    async def get_public_ip(self) -> Optional[str]:
        """Get public IP address using ifconfig.io"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.ifconfig_url, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                return data.get('ip')
        except Exception as e:
            print(f"Error getting public IP: {e}")
            return None
    
    async def get_geolocation_from_ip(self, ip_address: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get geolocation data from IP address"""
        try:
            target_ip = ip_address
            
            # If no IP provided or it's a private IP, get public IP first
            if not target_ip or await self.is_private_ip(target_ip):
                public_ip = await self.get_public_ip()
                if public_ip:
                    target_ip = public_ip
                else:
                    # Fallback: use ipapi.co without IP to get current location
                    target_ip = None
            
            # Build URL
            if target_ip:
                url = f"http://ipapi.co/{target_ip}/json/"
            else:
                url = self.ipapi_url
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                # Extract relevant location data
                location = {
                    'city': data.get('city'),
                    'region': data.get('region'),
                    'country': data.get('country_name'),
                    'latitude': data.get('latitude'),
                    'longitude': data.get('longitude'),
                    'ip': data.get('ip')
                }
                
                # Validate we have coordinates
                if location['latitude'] and location['longitude']:
                    return location
                else:
                    print("No coordinates found in geolocation data")
                    return None
                    
        except Exception as e:
            print(f"Error getting geolocation: {e}")
            return None
    
    async def get_geolocation_from_name(self, location_name: str) -> Optional[Dict[str, Any]]:
        """Get coordinates from location name using Open-Meteo's geocoding"""
        try:
            url = "https://geocoding-api.open-meteo.com/v1/search"
            params = {
                'name': location_name,
                'count': 1,
                'language': 'en',
                'format': 'json'
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                if data.get('results') and len(data['results']) > 0:
                    result = data['results'][0]
                    return {
                        'name': result.get('name'),
                        'country': result.get('country'),
                        'latitude': result.get('latitude'),
                        'longitude': result.get('longitude'),
                        'timezone': result.get('timezone')
                    }
                else:
                    print(f"No results found for location: {location_name}")
                    return None
                    
        except Exception as e:
            print(f"Error geocoding location name: {e}")
            return None

