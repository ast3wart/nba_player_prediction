# streamlit_app.py

import traceback
import streamlit as st
import pandas as pd
from src import prediction, utils, data_collection
import logging
from src.kg_utils import extract_context_subgraph
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import io  # Ensure io is imported
import tempfile
import os

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@st.cache_resource
def load_models(season='2023-24'):
    """
    Loads the ModelManager, including models and the Knowledge Graph.
    
    Parameters:
        season (str): NBA season to build the Knowledge Graph for.
    
    Returns:
        ModelManager: An instance of the ModelManager class with loaded models and KG.
    """
    try:
        model_manager = prediction.ModelManager(season=season)
        model_manager.load_models()             # Loads the entire pipelines
        model_manager.build_knowledge_graph()   # Builds the Knowledge Graph
        return model_manager
    except FileNotFoundError as e:
        logging.error(f"Model file missing: {e}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error loading models: {e}")
        raise

def visualize_subgraph(subgraph):
    """
    Visualizes a NetworkX subgraph using PyVis within Streamlit.
    Labels nodes with their actual names instead of IDs.

    Parameters:
        subgraph (networkx.Graph): The subgraph to visualize.
    """
    if subgraph.number_of_nodes() == 0:
        st.warning("No subgraph available to display.")
        return
    
    try:
        # Create a copy of the subgraph to avoid modifying the original
        subgraph_copy = subgraph.copy()
        
        # Iterate through each node and set the 'label' attribute
        for node, data in subgraph_copy.nodes(data=True):
            if 'name' in data:
                subgraph_copy.nodes[node]['label'] = data['name']
            else:
                # Fallback to node ID if 'name' attribute is missing
                subgraph_copy.nodes[node]['label'] = node
        
        # Initialize PyVis Network
        net = Network(height='500px', width='100%', notebook=False)
        net.from_nx(subgraph_copy)
        
        # Customize node appearance based on type
        for node, data in subgraph_copy.nodes(data=True):
            node_type = data.get('type')
            if node_type == 'Player':
                net.get_node(node)['color'] = 'blue'
                net.get_node(node)['shape'] = 'ellipse'
            elif node_type == 'Team':
                net.get_node(node)['color'] = 'green'
                net.get_node(node)['shape'] = 'box'
            elif node_type == 'Game':
                net.get_node(node)['color'] = 'red'
                net.get_node(node)['shape'] = 'diamond'
            elif node_type == 'Opponent_Team':  # New node type for Opponent
                net.get_node(node)['color'] = 'purple'
                net.get_node(node)['shape'] = 'dot'
            elif node_type == 'Home_Away':  # New node type for Home vs. Away games
                net.get_node(node)['color'] = 'orange'
                net.get_node(node)['shape'] = 'triangle'
            elif node_type == 'Performance':  # Performance metrics node
                net.get_node(node)['color'] = 'yellow'
                net.get_node(node)['shape'] = 'star'
        
        # Generate the graph HTML
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.html') as tmp_file:
            net.save_graph(tmp_file.name)
            tmp_file_path = tmp_file.name
        
        # Read the HTML content from the temporary file
        with open(tmp_file_path, 'r') as f:
            html = f.read()
        
        # Remove the temporary file
        os.remove(tmp_file_path)
        
        # Render the HTML in Streamlit
        components.html(html, height=550)
    except Exception as e:
        # Log the full traceback for detailed debugging
        logging.error(f"Error visualizing subgraph: {e}\n{traceback.format_exc()}")
        st.error("Error visualizing the Knowledge Graph subgraph.")

def main():
    st.title("NBA Player Performance Prediction")

    # User selects season first to load appropriate data
    season = st.selectbox('Select Season:', ['2023-24', '2022-23', '2021-22'], index=0)

    # Load models and related components
    try:
        model_manager = load_models(season=season)
    except FileNotFoundError as e:
        st.error(f"Model file missing: {e}")
        return
    except Exception as e:
        st.error(f"An unexpected error occurred while loading models: {e}")
        return

    player_name = st.text_input("Enter Player Name (e.g., LeBron James)")

    if player_name:
        # Fetch all players for the selected season
        players_df = data_collection.get_all_players(season)
        if players_df.empty:
            st.error(f"No player data available for season {season}.")
            return

        # Retrieve player ID
        player_id = utils.get_player_id(player_name, players_df)
        if player_id is None:
            st.error("Player not found. Please check the name and try again.")
            return

        # Fetch all NBA teams for the selected season
        all_teams = data_collection.get_team_data()
        if all_teams.empty:
            st.error("No team data available.")
            return

        team_abbreviations = all_teams['abbreviation'].tolist()
        opponent = st.selectbox("Select Opponent Team", team_abbreviations)

        st.header(f"Predict Performance for {player_name} against {opponent}")

        if st.button("Predict"):
            try:
                # Prepare input data
                X = prediction.prepare_input(player_name, opponent, season=season)

                # Make predictions using the pipelines
                reg_pred = prediction.predict_regression(X, model_manager.regressor_pipeline)
                clf_pred = prediction.predict_classification(X, model_manager.classifier_pipeline)

                # Display predictions
                st.write(f"**Predicted Points:** {reg_pred:.2f}")
                st.write(f"**Will Exceed Threshold:** {'Yes' if clf_pred == 1 else 'No'}")

                # Generate explanation using OpenAI
                prediction_result = {
                    'points': reg_pred,
                    'exceeds_threshold': bool(clf_pred)
                }

                # Extract context from KG
                subgraph = extract_context_subgraph(model_manager.KG, player_id, opponent, model_manager)
                player_team_abbr = model_manager.get_player_team(player_id)  # Updated line

                context_info = {
                    'Player': player_name,
                    'Player_Team': player_team_abbr if player_team_abbr else 'Unknown',
                    'Opponent': opponent,
                    'Relationships': [
                        {
                            'source': u,
                            'target': v,
                            'relation': d['relation']
                        }
                        for u, v, d in subgraph.edges(data=True)
                    ]
                }

                explanation = model_manager.generate_explanation(player_name, opponent, prediction_result, context_info)
                st.subheader("Prediction Explanation")
                st.write(explanation)

                # Visualize the subgraph
                st.subheader("Knowledge Graph Subgraph")
                visualize_subgraph(subgraph)

            except FileNotFoundError as e:
                st.error(f"Model file missing: {e}")
            except ValueError as e:
                st.error(f"Input error: {e}")
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}")
    else:
        st.info("Please enter a player's name to begin prediction.")

if __name__ == "__main__":
    main()