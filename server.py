from flask import Flask, render_template, request, redirect, session, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import random
from uuid import uuid4

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'
socketio = SocketIO(app, cors_allowed_origins="*")

# Database setup
def init_db():
    conn = sqlite3.connect('uno.db')
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE,
                  password TEXT)''')
    
    conn.commit()
    conn.close()

init_db()

# Uno game logic
class UnoGame:
    COLORS = ['red', 'blue', 'green', 'yellow']
    VALUES = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 
              'skip', 'reverse', 'draw2']
    WILD = ['wild', 'wild_draw4']
    
    def __init__(self):
        self.deck = self.create_deck()
        self.discard_pile = []
        self.players = []
        self.current_player = 0
        self.direction = 1  # 1 for clockwise, -1 for counter-clockwise
        self.status = 'waiting'
        
    def create_deck(self):
        deck = []
        # Add colored cards
        for color in self.COLORS:
            for value in self.VALUES:
                deck.append({'color': color, 'value': value})
                if value != '0':  # Only one zero per color
                    deck.append({'color': color, 'value': value})
        
        # Add wild cards (4 of each)
        for _ in range(4):
            for wild in self.WILD:
                deck.append({'color': 'wild', 'value': wild})
        
        random.shuffle(deck)
        return deck
    
    def deal_cards(self, num_players):
        hands = [[] for _ in range(num_players)]
        for _ in range(7):
            for i in range(num_players):
                if self.deck:
                    hands[i].append(self.deck.pop())
        return hands
    
    def start_game(self):
        if len(self.players) < 2:
            return False
        
        self.status = 'playing'
        hands = self.deal_cards(len(self.players))
        
        # Assign hands to players
        for i, player in enumerate(self.players):
            player['hand'] = hands[i]
        
        # First card
        while True:
            if not self.deck:
                self.deck = self.create_deck()
            card = self.deck.pop()
            if card['color'] != 'wild':  # First card can't be wild
                self.discard_pile.append(card)
                break
        
        return True
    
    def play_card(self, player_index, card_index, chosen_color=None):
        player = self.players[player_index]
        card = player['hand'][card_index]
        top_card = self.discard_pile[-1]
        
        # Check if move is valid
        valid = False
        if card['color'] == 'wild':
            valid = True
        elif card['color'] == top_card['color'] or card['value'] == top_card['value']:
            valid = True
            
        if not valid:
            return False
        
        # Remove card from player's hand and add to discard pile
        played_card = player['hand'].pop(card_index)
        self.discard_pile.append(played_card)
        
        # Handle special cards
        if played_card['color'] == 'wild':
            if chosen_color:
                played_card['color'] = chosen_color
            else:
                played_card['color'] = 'red'  # Default
        
        if played_card['value'] == 'skip':
            self.current_player = (self.current_player + self.direction) % len(self.players)
        elif played_card['value'] == 'reverse':
            self.direction *= -1
        elif played_card['value'] == 'draw2':
            next_player = (self.current_player + self.direction) % len(self.players)
            self.draw_cards(next_player, 2)
            self.current_player = (self.current_player + self.direction) % len(self.players)
        elif played_card['value'] == 'wild_draw4':
            next_player = (self.current_player + self.direction) % len(self.players)
            self.draw_cards(next_player, 4)
            self.current_player = (self.current_player + self.direction) % len(self.players)
        
        # Move to next player
        self.current_player = (self.current_player + self.direction) % len(self.players)
        
        # Check for winner
        if len(player['hand']) == 0:
            self.status = 'finished'
            return 'win'
        
        return True
    
    def draw_cards(self, player_index, num_cards):
        player = self.players[player_index]
        for _ in range(num_cards):
            if not self.deck:
                # Reshuffle discard pile (except top card) as new deck
                top_card = self.discard_pile.pop()
                self.deck = self.discard_pile
                self.discard_pile = [top_card]
                random.shuffle(self.deck)
            if self.deck:
                player['hand'].append(self.deck.pop())
    
    def draw_card(self, player_index):
        self.draw_cards(player_index, 1)
        # Player's turn ends after drawing
        self.current_player = (self.current_player + self.direction) % len(self.players)
        return True

    def remove_player(self, username):
        """Remove a player from the game"""
        self.players = [p for p in self.players if p['username'] != username]
        
        # If no players left, mark game for deletion
        if not self.players:
            self.status = 'empty'
            return True
        
        # Adjust current player index if needed
        if self.current_player >= len(self.players):
            self.current_player = 0
        
        # If less than 2 players remain, end the game
        if len(self.players) < 2 and self.status == 'playing':
            self.status = 'finished'
        
        return False

# User management routes
@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('lobby'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = sqlite3.connect('uno.db')
        c = conn.cursor()
        
        # Check if username exists
        c.execute('SELECT id FROM users WHERE username = ?', (username,))
        if c.fetchone():
            conn.close()
            return "Username already exists", 400
        
        # Create new user
        hashed_password = generate_password_hash(password)
        c.execute('INSERT INTO users (username, password) VALUES (?, ?)',
                 (username, hashed_password))
        conn.commit()
        conn.close()
        
        session['username'] = username
        return redirect(url_for('lobby'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = sqlite3.connect('uno.db')
        c = conn.cursor()
        
        c.execute('SELECT id, password FROM users WHERE username = ?', (username,))
        user = c.fetchone()
        conn.close()
        
        if user and check_password_hash(user[1], password):
            session['username'] = username
            return redirect(url_for('lobby'))
        else:
            return "Invalid username or password", 401
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('index'))

@app.route('/lobby')
def lobby():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('lobby.html', username=session['username'])

# Game management
active_games = {}

@app.route('/game/<game_id>')
def game(game_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    if game_id not in active_games:
        return redirect(url_for('lobby'))
    
    game = active_games[game_id]
    player_index = next((i for i, p in enumerate(game.players) if p['username'] == session['username']), None)
    
    if player_index is None:
        return redirect(url_for('lobby'))
    
    return render_template('game.html', game_id=game_id, player_index=player_index)

# SocketIO events
@socketio.on('connect')
def handle_connect():
    if 'username' in session:
        emit('get_games')

@socketio.on('disconnect')
def handle_disconnect():
    if 'username' in session:
        # Remove player from any game they're in
        username = session['username']
        for game_id, game in list(active_games.items()):
            if any(p['username'] == username for p in game.players):
                leave_room(game_id)
                if game.remove_player(username):
                    # Game is empty, delete it
                    del active_games[game_id]
                else:
                    # Update remaining players
                    emit('player_left', {'username': username}, room=game_id)
                    if game.status == 'playing':
                        update_game_state(game_id)
                break
        
        # Update lobby
        handle_get_games()

@socketio.on('get_games')
def handle_get_games():
    """Send list of available games to the client"""
    available_games = []
    for game_id, game in active_games.items():
        if game.status == 'waiting':
            available_games.append({
                'game_id': game_id,
                'players': [p['username'] for p in game.players],
                'player_count': len(game.players)
            })
    emit('games_list', {'games': available_games})

@socketio.on('create_game')
def handle_create_game():
    if 'username' not in session:
        return
    
    game_id = str(uuid4())
    game = UnoGame()
    game.players.append({'username': session['username'], 'hand': []})
    active_games[game_id] = game
    
    emit('game_created', {'game_id': game_id})
    socketio.emit('games_list_update')

@socketio.on('join_game')
def handle_join_game(data):
    if 'username' not in session:
        emit('join_error', {'message': 'Not authenticated'})
        return
    
    game_id = data.get('game_id')
    if game_id not in active_games:
        emit('join_error', {'message': 'Game not found'})
        return
    
    game = active_games[game_id]
    
    # Check if player already in game
    if any(p['username'] == session['username'] for p in game.players):
        emit('join_error', {'message': 'Already in this game'})
        return
    
    # Check if game is full (max 6 players)
    if len(game.players) >= 6:
        emit('join_error', {'message': 'Game is full'})
        return
    
    # Add player to game
    game.players.append({'username': session['username'], 'hand': []})
    join_room(game_id)
    
    # Notify all players in the game
    emit('player_joined', {
        'username': session['username'],
        'player_count': len(game.players),
        'players': [p['username'] for p in game.players]
    }, room=game_id)
    
    # Update games list for everyone
    socketio.emit('games_list_update')
    
    # Let the joining player know it was successful
    emit('join_success', {'game_id': game_id})

@socketio.on('leave_game')
def handle_leave_game(data):
    if 'username' not in session:
        return
    
    game_id = data.get('game_id')
    if game_id not in active_games:
        return
    
    game = active_games[game_id]
    username = session['username']
    
    leave_room(game_id)
    
    if game.remove_player(username):
        # Game is empty, delete it
        del active_games[game_id]
    else:
        # Notify remaining players
        emit('player_left', {
            'username': username,
            'player_count': len(game.players),
            'players': [p['username'] for p in game.players]
        }, room=game_id)
        
        if game.status == 'playing':
            update_game_state(game_id)
    
    # Update games list
    socketio.emit('games_list_update')

@socketio.on('start_game')
def handle_start_game(data):
    game_id = data.get('game_id')
    if game_id not in active_games:
        return
    
    game = active_games[game_id]
    
    # Only allow game creator to start
    if game.players[0]['username'] != session['username']:
        return
    
    # Need at least 2 players
    if len(game.players) < 2:
        emit('error', {'message': 'Need at least 2 players to start'})
        return
    
    if game.start_game():
        # Send game state to all players
        update_game_state(game_id)
        
        # Update games list (remove from available games)
        socketio.emit('games_list_update')

@socketio.on('play_card')
def handle_play_card(data):
    game_id = data.get('game_id')
    if game_id not in active_games:
        return
    
    game = active_games[game_id]
    player_index = data.get('player_index')
    card_index = data.get('card_index')
    chosen_color = data.get('chosen_color')
    
    # Validate it's the player's turn
    if game.current_player != player_index:
        emit('error', {'message': 'Not your turn'})
        return
    
    result = game.play_card(player_index, card_index, chosen_color)
    
    if result == 'win':
        emit('game_over', {'winner': game.players[player_index]['username']}, room=game_id)
    elif result:
        # Update all players instantly
        update_game_state(game_id)
    else:
        emit('error', {'message': 'Invalid move'})

@socketio.on('draw_card')
def handle_draw_card(data):
    game_id = data.get('game_id')
    if game_id not in active_games:
        return
    
    game = active_games[game_id]
    player_index = data.get('player_index')
    
    # Validate it's the player's turn
    if game.current_player != player_index:
        emit('error', {'message': 'Not your turn'})
        return
    
    game.draw_card(player_index)
    update_game_state(game_id)

def update_game_state(game_id):
    if game_id not in active_games:
        return
        
    game = active_games[game_id]
    
    game_state = {
        'current_player': game.current_player,
        'direction': game.direction,
        'top_card': game.discard_pile[-1] if game.discard_pile else None,
        'status': game.status,
        'player_count': len(game.players),
        'cards_in_deck': len(game.deck),
        'players': [{'username': p['username'], 'card_count': len(p['hand'])} for p in game.players]
    }
    
    # Send each player their updated hand and game state
    for i, player in enumerate(game.players):
        emit('game_update', {
            **game_state,
            'your_index': i,
            'your_hand': player['hand']
        }, room=game_id)

if __name__ == '__main__':
    socketio.run(app, debug=True)