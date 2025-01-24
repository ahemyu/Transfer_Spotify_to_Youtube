import os
from pathlib import Path
import json
from time import sleep
from typing import List, Dict, Optional
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import spotipy
from spotipy.oauth2 import SpotifyOAuth

YOUTUBE_SCOPES = ['https://www.googleapis.com/auth/youtube']

def get_youtube_client():
    """Set up YouTube API client with OAuth2"""
    flow = InstalledAppFlow.from_client_secrets_file(
        os.getenv("secret"),
        scopes=YOUTUBE_SCOPES
    )
    credentials = flow.run_local_server(port=9000)
    return build('youtube', 'v3', credentials=credentials)

def get_spotify_client():
    """Set up Spotify client with OAuth"""
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id = "YOUR ID HERE",
        client_secret="YOUR SECRET HERE",
        redirect_uri='http://localhost:8888/callback',
        scope='playlist-read-private user-library-read'
    ))

def save_progress(playlist_id: str, processed_tracks: List[str], 
                 remaining_tracks: List[Dict[str, any]]) -> None:
    """
    Save current progress to a JSON file
    
    Args:
        playlist_id: YouTube playlist ID
        processed_tracks: List of track names that were successfully added
        remaining_tracks: List of tracks that still need to be processed
    """
    progress = {
        'playlist_id': playlist_id,
        'processed_tracks': processed_tracks,
        'remaining_tracks': remaining_tracks
    }
    
    with open('playlist_progress.json', 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

def load_progress() -> Optional[Dict]:
    """
    Load saved progress if it exists
    
    Returns:
        Dict containing progress data if file exists, None otherwise
    """
    progress_file = Path('playlist_progress.json')
    if progress_file.exists():
        with open(progress_file, encoding='utf-8') as f:
            return json.load(f)
    return None

def get_spotify_tracks(sp: spotipy.Spotify, playlist_id: str | None = None, 
                      liked_songs: bool = False) -> List[Dict[str, any]]:
    """
    Get tracks from either a Spotify playlist or Liked Songs
    
    Args:
        sp: Spotify client
        playlist_id: ID of the playlist (None if getting liked songs)
        liked_songs: If True, get Liked Songs instead of playlist
        
    Returns:
        List of tracks with name and artists
    """
    if liked_songs:
        results = sp.current_user_saved_tracks()
    else:
        results = sp.playlist_tracks(playlist_id)
        
    tracks = []
    
    while results:
        for item in results['items']:
            # For liked songs, the track is directly in 'track' key
            track = item['track']
            if track:  # Check if track exists and has required fields
                try:
                    tracks.append({
                        'name': track['name'],
                        'artists': [artist['name'] for artist in track['artists']]
                    })
                except KeyError as e:
                    print(f"Skipping track due to missing data: {e}")
                    continue
        
        if results['next']:
            if liked_songs:
                results = sp.current_user_saved_tracks(offset=len(tracks))
            else:
                results = sp.next(results)
        else:
            results = None
    
    return tracks

def create_or_get_youtube_playlist(youtube: any, playlist_name: str | None = None, 
                                 playlist_id: str | None = None, 
                                 description: str = "Imported from Spotify") -> Dict:
    """
    Create a new YouTube playlist or get an existing one
    
    Args:
        youtube: YouTube API client
        playlist_name: Name for new playlist (used only if playlist_id is None)
        playlist_id: ID of existing playlist (if provided, will use this instead of creating new)
        description: Description for new playlist (used only if playlist_id is None)
        
    Returns:
        Dict containing playlist information including ID
    """
    if playlist_id:
        try:
            # Try to get existing playlist
            request = youtube.playlists().list(
                part="snippet,status",
                id=playlist_id
            )
            response = request.execute()
            
            if response.get('items'):
                print(f"Using existing playlist with ID: {playlist_id}")
                return response['items'][0]
            else:
                raise ValueError(f"No playlist found with ID: {playlist_id}")
                
        except HttpError as e:
            print(f"Error accessing playlist: {str(e)}")
            raise
    
    elif playlist_name:
        # Create new playlist
        request = youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": playlist_name,
                    "description": description
                },
                "status": {
                    "privacyStatus": "private"
                }
            }
        )
        return request.execute()
    
    else:
        raise ValueError("Either playlist_name or playlist_id must be provided")

def search_and_add_to_playlist(youtube: any, playlist_id: str, tracks: List[Dict[str, any]], 
                             max_retries: int = 3, delay: int = 2) -> None:
    """
    Search for songs and add them to YouTube playlist with progress saving
    """
    processed_tracks = []
    remaining_tracks = tracks.copy()
    
    # Load previous progress if exists
    progress = load_progress()
    if progress and progress['playlist_id'] == playlist_id:
        print("Resuming from previous progress...")
        processed_tracks = progress['processed_tracks']
        remaining_tracks = progress['remaining_tracks']
        print(f"Previously processed: {len(processed_tracks)} tracks")
    
    for track in remaining_tracks[:]:  # Create a copy to iterate over
        retry_count = 0
        while retry_count < max_retries:
            try:
                query = f"{track['name']} {' '.join(track['artists'])}"
                
                search_response = youtube.search().list(
                    q=query,
                    part="id,snippet",
                    maxResults=1,
                    type="video"
                ).execute()

                if not search_response.get('items'):
                    print(f"No results found for: {track['name']}")
                    break

                video_id = search_response['items'][0]['id']['videoId']
                
                youtube.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": playlist_id,
                            "resourceId": {
                                "kind": "youtube#video",
                                "videoId": video_id
                            }
                        }
                    }
                ).execute()
                
                print(f"Added: {track['name']}")
                processed_tracks.append(track['name'])
                remaining_tracks.remove(track)
                save_progress(playlist_id, processed_tracks, remaining_tracks)
                sleep(delay)
                break
                
            except HttpError as e:
                if "quotaExceeded" in str(e):
                    print("\nYouTube API quota exceeded. Progress has been saved.")
                    print(f"Successfully added {len(processed_tracks)} tracks.")
                    print("Please try again tomorrow when the quota resets.")
                    save_progress(playlist_id, processed_tracks, remaining_tracks)
                    return  # Exit the function
                
                retry_count += 1
                if retry_count == max_retries:
                    print(f"Failed to add {track['name']} after {max_retries} attempts. Error: {str(e)}")
                else:
                    print(f"Attempt {retry_count} failed for {track['name']}. Retrying...")
                    sleep(delay * 2)
                    
            except Exception as e:
                print(f"Unexpected error while adding {track['name']}: {str(e)}")
                break

    if not remaining_tracks:
        print("\nAll tracks processed successfully!")
        # Clean up progress file if everything is done
        if Path('playlist_progress.json').exists():
            Path('playlist_progress.json').unlink()

def transfer_playlist(spotify_playlist_id: str | None = None, 
                     youtube_playlist_name: str | None = None,
                     youtube_playlist_id: str | None = None,
                     spotify_liked_songs: bool = False) -> None:
    """
    Main function to transfer playlist with resume capability
    
    Args:
        spotify_playlist_id: ID of the source Spotify playlist (None if using liked songs)
        youtube_playlist_name: Name for new YouTube playlist (if youtube_playlist_id not provided)
        youtube_playlist_id: ID of existing YouTube playlist (optional)
        spotify_liked_songs: If True, transfer Liked Songs instead of a playlist
    """
    if not youtube_playlist_name and not youtube_playlist_id:
        raise ValueError("Either youtube_playlist_name or youtube_playlist_id must be provided")
    
    if not spotify_playlist_id and not spotify_liked_songs:
        raise ValueError("Either spotify_playlist_id must be provided or spotify_liked_songs must be True")
        
    youtube = get_youtube_client()
    spotify = get_spotify_client()
    
    # Check for existing progress
    progress = load_progress()
    if progress:
        print("Found saved progress. Using existing YouTube playlist.")
        playlist_id = progress['playlist_id']
        tracks = progress['remaining_tracks']
    else:
        print("Getting tracks from Spotify...")
        tracks = get_spotify_tracks(spotify, playlist_id=spotify_playlist_id, liked_songs=spotify_liked_songs)
        
        print("Creating/getting YouTube playlist...")
        playlist = create_or_get_youtube_playlist(
            youtube, 
            playlist_name=youtube_playlist_name,
            playlist_id=youtube_playlist_id
        )
        playlist_id = playlist['id']
    
    print("Adding tracks to YouTube playlist...")
    search_and_add_to_playlist(youtube, playlist_id, tracks)

if __name__ == "__main__":
    # Example usage
    
    # Option 1: Transfer a playlist
    # transfer_playlist(
    #     spotify_playlist_id="your_spotify_playlist_id",
    #     youtube_playlist_name="New Playlist Name"
    # )
    
    # # Option 2: Transfer Liked Songs to a new playlist
    # transfer_playlist(
    #     spotify_liked_songs=True,
    #     youtube_playlist_name="My Liked Songs from Spotify"
    # )
    
    # Option 3: Transfer Liked Songs to an existing playlist
    transfer_playlist(
        spotify_liked_songs=True,
        youtube_playlist_id="PL7QxQEBBBDnY8KpejZ-MegzVzKqMWtDuE"
    )