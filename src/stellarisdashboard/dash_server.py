import logging
import time
from typing import Dict, Any, List
from urllib import parse

import dash
import dash_core_components as dcc
import dash_html_components as html
import flask
import plotly.graph_objs as go
from dash.dependencies import Input, Output
from flask import render_template, request, redirect

from stellarisdashboard import config, models, visualization_data, game_info

logger = logging.getLogger(__name__)

flask_app = flask.Flask(__name__)
flask_app.logger.setLevel(logging.DEBUG)
timeline_app = dash.Dash(name="Stellaris Timeline", server=flask_app, compress=False, url_base_pathname="/timeline/")
timeline_app.css.config.serve_locally = True
timeline_app.scripts.config.serve_locally = True

VERSION_ID = "v0.1.5"


def is_old_version(requested_version: str) -> bool:
    return requested_version != VERSION_ID


@flask_app.route("/")
@flask_app.route("/checkversion/<version>/")
def index_page(version=None):
    show_old_version_notice = False
    if version is not None:
        show_old_version_notice = is_old_version(version)
    games = [dict(country=country, game_name=g) for g, country in models.get_available_games_dict().items()]
    return render_template(
        "index.html",
        games=games,
        show_old_version_notice=show_old_version_notice,
        version=VERSION_ID,
    )


@flask_app.route("/history")
@flask_app.route("/history/<game_id>")
@flask_app.route("/checkversion/<version>/history")
@flask_app.route("/checkversion/<version>/history/<game_id>")
def history_page(
        game_id=None,
        version=None,
):
    show_old_version_notice = False
    if version is not None:
        show_old_version_notice = is_old_version(version)
    if game_id is None:
        game_id = ""

    games_dict = models.get_available_games_dict()
    matches = models.get_known_games(game_id)
    if not matches:
        logger.warning(f"Could not find a game matching {game_id}")
        return render_template("404_page.html", game_not_found=True, game_name=game_id)
    game_id = matches[0]
    country = games_dict[game_id]

    country_id = request.args.get("country", None)
    leader_id = request.args.get("leader", None)
    system_id = request.args.get("system", None)
    min_date = request.args.get("min_date", float("-inf"))
    is_filtered_page = any([country_id, leader_id, system_id])
    page_title = "Global Event Ledger"
    with models.get_db_session(game_id) as session:
        if is_filtered_page:
            page_title = f"History {country_id} {leader_id} {system_id}"
        date = get_most_recent_date(session)
        wars = []
        if not is_filtered_page:
            wars = get_war_dicts(session, date)
        events, country_details, preformatted_links = get_event_and_link_dicts(
            session,
            game_id,
            event_filter=EventFilter(
                min_date=min_date,
                country_filter=country_id,
                leader_filter=leader_id,
                system_filter=system_id
            ),
        )
    return render_template(
        "history_page.html",
        page_title=page_title,
        game_name=game_id,
        country=country,
        wars=wars,
        events=events,
        country_details=country_details,
        links=preformatted_links,
        is_filtered_page=is_filtered_page,
        show_old_version_notice=show_old_version_notice,
        version=VERSION_ID,
    )


@flask_app.route("/country/<country_id>")
def country(country_id=None):
    return f"Hello Country {country_id}"


@flask_app.route("/system/<system_id>")
def system(system_id=None):
    return f"Hello System {system_id}"


@flask_app.route("/settings/")
@flask_app.route("/settings")
def settings_page():
    def _convert_python_bool_to_lowercase(py_bool):
        return "true" if py_bool else "false"

    t_int = "int"
    t_bool = "bool"
    t_str = "str"
    current_settings = config.CONFIG.get_adjustable_settings_dict()
    settings_with_descriptions = {
        "check_version": {
            "type": t_bool,
            "value": _convert_python_bool_to_lowercase(current_settings["check_version"]),
            "name": "Check for new versions",
            "description": "Check if new versions of the dashboard are available. This only works if you subscribe to the mod in the Steam workshop.",
        },
        "extract_system_ownership": {
            "type": t_bool,
            "value": _convert_python_bool_to_lowercase(current_settings["extract_system_ownership"]),
            "name": "Extract system ownership",
            "description": "Extracting ownership of systems can be slow. If this setting is used, the historical galaxy map won't show any empires.",
        },
        "show_everything": {
            "type": t_bool,
            "value": _convert_python_bool_to_lowercase(current_settings["show_everything"]),
            "name": "Cheat mode: Show all empires",
            "description": "Cheat mode: Show data for all empires, regardless of diplomatic status, even if you haven't met them in-game.",
        },
        "only_show_default_empires": {
            "type": t_bool,
            "value": _convert_python_bool_to_lowercase(current_settings["only_show_default_empires"]),
            "name": "Only show default empires",
            "description": 'Only show default-class empires, i.e. normal countries. Use it to exclude fallen empires and similar. Usually, this setting only matters if you have the Cheat mode enabled.',
        },
        "only_read_player_history": {
            "type": t_bool,
            "value": _convert_python_bool_to_lowercase(current_settings["only_read_player_history"]),
            "name": "Only extract player history",
            "description": "Only extract your country's history for the event ledger. Reduces workload and database size, but you won't have access to the full game history.",
        },
        "allow_backdating": {
            "type": t_bool,
            "value": _convert_python_bool_to_lowercase(current_settings["allow_backdating"]),
            "name": "Back-date initial events",
            "description": "If active, events that occur before the game starts are back-dated by an appropriate, randomized amount. If inactive, all initial events happen on (or near) 2200.01.01, which may look odd.",
        },
        "save_name_filter": {
            "type": t_str,
            "value": current_settings["save_name_filter"],
            "name": "Save file name filter",
            "description": "Save files whose file names do not contain this string are ignored. For example, you can use it to only read yearly autosaves, by setting the value to \".01.01.sav\"",
        },
        "read_only_every_nth_save": {
            "type": t_int,
            "value": current_settings["read_only_every_nth_save"],
            "max": 10,
            "name": "Only read every n-th save",
            "description": "Set to 2 to ignore every other save, to 3 to ignore 2/3 of saves, and so on. This is applied after all other filters.",
        },
        "threads": {
            "type": t_int,
            "value": current_settings["threads"],
            "max": config.CPU_COUNT,
            "name": "Number of CPU cores",
            "description": "Maximal number of CPU cores used for reading save files.",
        },
    }

    return render_template(
        "settings_page.html",
        current_settings=settings_with_descriptions,
    )


@flask_app.route("/applysettings/", methods=["POST", "GET"])
def apply_settings():
    previous_settings = config.CONFIG.get_adjustable_settings_dict()
    settings = request.form.to_dict(flat=True)
    print(settings)
    for key in settings:
        if key in config.Config.BOOL_KEYS:
            settings[key] = key in settings  # only checked items are included in form data
        if key in config.Config.INT_KEYS:
            settings[key] = int(settings[key])
    for key in previous_settings:
        if key in config.Config.BOOL_KEYS and key not in settings:
            settings[key] = False
    config.CONFIG.apply_dict(settings)
    config.CONFIG.write_to_file()
    print("Updated configuration:")
    print(config.CONFIG)
    return redirect("/")


DARK_THEME_BACKGROUND = 'rgba(33,43,39,1)'
DARK_THEME_GALAXY_BACKGROUND = 'rgba(0,0,0,1)'
DARK_THEME_BACKGROUND_DARK = 'rgba(20,25,25,1)'
BACKGROUND_PLOT_DARK = 'rgba(43,59,52,1)'
DARK_THEME_TEXT_COLOR = 'rgba(217,217,217,1)'
DARK_THEME_TEXT_HIGHLIGHT_COLOR = 'rgba(195, 133, 33, 1)'
DEFAULT_PLOT_LAYOUT = dict(
    yaxis=dict(
        type="linear",
    ),
    height=640,
    plot_bgcolor=BACKGROUND_PLOT_DARK,
    paper_bgcolor=DARK_THEME_BACKGROUND,
    font={'color': DARK_THEME_TEXT_COLOR},
)

# SOME CSS ATTRIBUTES
BUTTON_STYLE = {
    "color": DARK_THEME_TEXT_HIGHLIGHT_COLOR,
    "font-family": "verdana",
    "font-size": "20px",
    "-webkit-appearance": "button",
    "-moz-appearance": "button",
    "appearance": "button",
    "background-color": BACKGROUND_PLOT_DARK,
    "display": "inline",
    "text-decoration": "none",
    "padding": "0.1cm",
    "margin": "0.1cm",
}
HEADER_STYLE = {
    "font-family": "verdana",
    "color": DARK_THEME_TEXT_COLOR,
    "margin-top": "20px",
    "margin-bottom": "10px",
    "text-align": "center",
}
TEXT_STYLE = {
    "font-family": "verdana",
    "color": "rgba(217, 217, 217, 1)",
}

SELECTED_TAB_STYLE = {
    'width': 'inherit',
    'boxShadow': 'none',
    'borderLeft': 'thin lightgrey solid',
    'borderRight': 'thin lightgrey solid',
    'borderTop': '2px #0074D9 solid',
    'background': DARK_THEME_BACKGROUND,
    'color': DARK_THEME_TEXT_HIGHLIGHT_COLOR,
}
TAB_CONTAINER_STYLE = {
    'width': 'inherit',
    'boxShadow': 'inset 0px -1px 0px 0px lightgrey',
    'background': DARK_THEME_BACKGROUND
}
TAB_STYLE = {
    'width': 'inherit',
    'border': 'none',
    'boxShadow': 'inset 0px -1px 0px 0px lightgrey',
    'background': DARK_THEME_BACKGROUND_DARK,
    'color': DARK_THEME_TEXT_COLOR,
}

CATEGORY_TABS = [category for category in visualization_data.THEMATICALLY_GROUPED_PLOTS]
CATEGORY_TABS.append("Galaxy")

DEFAULT_SELECTED_CATEGORY = "Budget"

timeline_app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
    html.Div([
        html.Div([
            html.A("Go to Game Selection", id='index-link', href="/", style=BUTTON_STYLE),
            html.A(f'Dashboard Settings', id='settings-link', href="/settings/", style=BUTTON_STYLE),
            html.A(f'Go to Event Ledger', id='ledger-link', href="/", style=BUTTON_STYLE),
        ]),
        html.H1(children="Unknown Game", id="game-name-header", style=HEADER_STYLE),
        dcc.Tabs(
            id='tabs-container',
            style=TAB_CONTAINER_STYLE,
            parent_style=TAB_CONTAINER_STYLE,
            children=[dcc.Tab(id=tab_label,
                              label=tab_label,
                              value=tab_label,
                              style=TAB_STYLE,
                              selected_style=SELECTED_TAB_STYLE)
                      for tab_label in CATEGORY_TABS],
            value=DEFAULT_SELECTED_CATEGORY,
        ),
        html.Div(id='tab-content', style={
            'width': '100%',
            'height': '100%',
            'margin-left': 'auto',
            'margin-right': 'auto'
        }),
        dcc.Slider(
            id='dateslider',
            min=0,
            max=100,
            step=0.01,
            value=100,
            # updatemode='drag',
            marks={i: '{}%'.format(i) for i in range(0, 110, 10)},
        ),
    ], style={
        'width': '100%',
        "height": "100%",
        'fontFamily': 'Sans-Serif',
        'margin-left': 'auto',
        'margin-right': 'auto'
    }),
], style={
    "width": "100%",
    "height": "100%",
    "padding": 0,
    "margin": 0,
    "background-color": DARK_THEME_BACKGROUND,
})


def get_figure_layout(plot_spec: visualization_data.PlotSpecification):
    layout = DEFAULT_PLOT_LAYOUT
    if plot_spec.style == visualization_data.PlotStyle.line:
        layout["hovermode"] = "closest"
    else:
        layout["hovermode"] = "x"
    return go.Layout(**layout)


@timeline_app.callback(Output('ledger-link', 'href'),
                       [Input('url', 'search')])
def update_ledger_link(search):
    game_id, _ = _get_game_ids_matching_url(search)
    return flask.url_for("history_page", game_id=game_id)


@timeline_app.callback(Output('game-name-header', 'children'),
                       [Input('url', 'search')])
def update_game_header(search):
    game_id, matches = _get_game_ids_matching_url(search)
    if not matches:
        logger.warning(f"Could not find a game matching {game_id}")
        return "Unknown Game"
    game_id = matches[0]
    games_dict = models.get_available_games_dict()
    return f"{games_dict[game_id]} ({game_id})"


@timeline_app.callback(Output('tab-content', 'children'),
                       [Input('tabs-container', 'value'), Input('url', 'search'), Input('dateslider', 'value')])
def update_content(tab_value, search, date_fraction):
    game_id, matches = _get_game_ids_matching_url(search)
    if not matches:
        logger.warning(f"Could not find a game matching {game_id}")
        return render_template("404_page.html", game_not_found=True, game_name=game_id)

    games_dict = models.get_available_games_dict()
    game_id = matches[0]
    if game_id not in games_dict:
        logger.warning(f"Game ID {game_id} does not match any known game!")
        return []

    logger.info(f"dash_server.update_content: Tab is {tab_value}, Game is {game_id}")
    with models.get_db_session(game_id) as session:
        current_date = get_most_recent_date(session)

    children = []
    if tab_value in visualization_data.THEMATICALLY_GROUPED_PLOTS:
        plots = visualization_data.THEMATICALLY_GROUPED_PLOTS[tab_value]
        for plot_spec in plots:
            figure_data = get_figure_data(game_id, plot_spec)
            figure_layout = get_figure_layout(plot_spec)
            figure = go.Figure(data=figure_data, layout=figure_layout)

            children.append(html.H2(f"{plot_spec.title}", style=HEADER_STYLE))
            children.append(dcc.Graph(
                id=f"{plot_spec.plot_id}",
                figure=figure,
            ))
    else:
        slider_date = 0.01 * date_fraction * current_date
        children.append(get_galaxy(game_id, slider_date))
        children.append(html.P(f"Galactic Records for {models.days_to_date(slider_date)}", style=TEXT_STYLE))
    return children


def _get_game_ids_matching_url(url):
    game_id = parse.parse_qs(parse.urlparse(url).query).get("game_name", [None])[0]
    if game_id is None:
        game_id = ""
    matches = models.get_known_games(game_id)
    return game_id, matches


def get_plot_lines(plot_data: visualization_data.EmpireProgressionPlotData, plot_spec: visualization_data.PlotSpecification) -> List[Dict[str, Any]]:
    if plot_spec.style == visualization_data.PlotStyle.line:
        plot_list = _get_line_plot_data(plot_data, plot_spec)
    elif plot_spec.style == visualization_data.PlotStyle.stacked:
        plot_list = _get_stacked_plot_data(plot_data, plot_spec)
    elif plot_spec.style == visualization_data.PlotStyle.budget:
        plot_list = _get_budget_plot_data(plot_data, plot_spec)
    else:
        logger.warning(f"Unknown Plot type {plot_spec}")
        plot_list = []
    return plot_list


def _get_line_plot_data(plot_data: visualization_data.EmpireProgressionPlotData, plot_spec: visualization_data.PlotSpecification):
    plot_list = []
    for key, x_values, y_values in plot_data.data_sorted_by_last_value(plot_spec):
        if not any(y_values):
            continue
        line = dict(
            x=x_values,
            y=y_values,
            name=key,
            text=[f"{val:.2f} - {key}" for val in y_values],
            line={"color": get_country_color(key, 1.0)},
        )
        plot_list.append(line)
    return plot_list


def _get_stacked_plot_data(plot_data: visualization_data.EmpireProgressionPlotData, plot_spec: visualization_data.PlotSpecification):
    y_previous = None
    plot_list = []
    for key, x_values, y_values in plot_data.iterate_data(plot_spec):
        if not any(y_values):
            continue
        line = {'x': x_values, 'name': key, "fill": "tonexty", "hoverinfo": "x+text"}
        if y_previous is None:
            y_previous = [0.0 for _ in x_values]
        y_previous = [(a + b) for a, b in zip(y_previous, y_values)]
        line["y"] = y_previous[:]  # make a copy
        if line["y"]:
            line["text"] = [f"{val:.2f} - {key}" if val else "" for val in y_values]
            line["line"] = {"color": get_country_color(key, 1.0)}
            line["fillcolor"] = get_country_color(key, 0.75)
            plot_list.append(line)
    return plot_list


def _get_budget_plot_data(plot_data: visualization_data.EmpireProgressionPlotData, plot_spec: visualization_data.PlotSpecification):
    net_gain = None
    y_previous_pos, y_previous_neg = None, None
    pos_initiated = False
    plot_list = []
    for key, x_values, y_values in plot_data.data_sorted_by_last_value(plot_spec):
        if not any(y_values):
            continue
        if net_gain is None:
            net_gain = [0.0 for _ in x_values]
            y_previous_pos = [0.0 for _ in x_values]
            y_previous_neg = [0.0 for _ in x_values]
        fill_mode = "tozeroy"
        if all(y <= 0 for y in y_values):
            y_previous = y_previous_neg
        elif all(y >= 0 for y in y_values):
            y_previous = y_previous_pos
            if pos_initiated:
                fill_mode = "tonexty"
            pos_initiated = True
        else:
            logger.warning("Not a real budget Graph!")
            break
        line = {'x': x_values, 'name': key, "hoverinfo": "x+text"}
        for i, y in enumerate(y_values):
            y_previous[i] += y
            net_gain[i] += y
        line["y"] = y_previous[:]
        line["fill"] = fill_mode
        line["line"] = {"color": get_country_color(key, 1.0)}
        line["fillcolor"] = get_country_color(key, 0.3)
        line["text"] = [f"{val:.2f} - {key}" if val else "" for val in y_values]
        plot_list.append(line)
    if plot_list:
        plot_list.append({
            'x': plot_list[0]["x"],
            'y': net_gain,
            'name': 'Net gain',
            'line': {'color': 'rgba(255,255,255,1)'},
            'text': [f'{val:.2f} - net gain' for val in net_gain],
            'hoverinfo': 'x+text',
        })
    return plot_list


def get_galaxy(game_id, date):
    # adapted from https://plot.ly/python/network-graphs/
    galaxy = visualization_data.get_galaxy_data(game_id)
    graph = galaxy.get_graph_for_date(date)
    edge_traces_data = {}
    for edge in graph.edges:
        country = graph.edges[edge]["country"]
        if country not in edge_traces_data:
            width = 1 if country == visualization_data.GalaxyMapData.UNCLAIMED else 8
            edge_traces_data[country] = dict(
                x=[],
                y=[],
                text=[],
                line=go.scatter.Line(width=width, color=get_country_color(country)),
                hoverinfo='text',
                mode='lines',
                showlegend=False,
            )
        x0, y0 = graph.nodes[edge[0]]['pos']
        x1, y1 = graph.nodes[edge[1]]['pos']
        # insert None to prevent dash from rendering a single lingle
        edge_traces_data[country]['x'] += [x0, x1, None]
        edge_traces_data[country]['y'] += [y0, y1, None]
        edge_traces_data[country]['text'] += [country]
    edge_traces = {country: go.Scatter(**edge_traces_data[country]) for country in edge_traces_data}

    node_traces_data = {}
    for node in graph.nodes:
        country = graph.nodes[node]["country"]
        if country not in node_traces_data:
            node_size = 10 if country != visualization_data.GalaxyMapData.UNCLAIMED else 4
            node_traces_data[country] = dict(
                x=[], y=[],
                text=[],
                mode='markers',
                hoverinfo='text',
                marker=dict(
                    color=[],
                    size=node_size,
                    line=dict(width=0.5)),
                name=country,
            )
        color = get_country_color(country)
        node_traces_data[country]['marker']['color'].append(color)
        x, y = graph.nodes[node]['pos']
        node_traces_data[country]['x'].append(x)
        node_traces_data[country]['y'].append(y)
        country_str = f" ({country})" if country != visualization_data.GalaxyMapData.UNCLAIMED else ""
        node_traces_data[country]['text'].append(f'{graph.nodes[node]["name"]}{country_str}')

    for country in node_traces_data:
        # convert markers first:
        node_traces_data[country]["marker"] = go.scatter.Marker(**node_traces_data[country]["marker"])

    node_traces = {country: go.Scatter(**node_traces_data[country]) for country in node_traces_data}

    layout = go.Layout(
        xaxis=go.layout.XAxis(
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            fixedrange=True,
        ),
        yaxis=go.layout.YAxis(
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            fixedrange=True,
        ),
        margin=dict(
            t=0, b=0, l=0, r=0,
        ),
        legend=dict(
            orientation="v",
            x=1.0,
            y=1.0,
        ),
        height=720,
        hovermode='closest',
        plot_bgcolor=DARK_THEME_GALAXY_BACKGROUND,
        paper_bgcolor=BACKGROUND_PLOT_DARK,
        font={'color': DARK_THEME_TEXT_COLOR},

    )

    return dcc.Graph(
        id="galaxy-map",
        figure=go.Figure(
            data=(list(edge_traces.values()) + list(node_traces.values())),
            layout=layout,
        ),
    )


class EventFilter:
    def __init__(self,
                 min_date=float("-inf"),
                 max_date=float("inf"),
                 country_filter=None,
                 type_filter=None,
                 war_filter=None,
                 leader_filter=None,
                 system_filter=None,
                 planet_filter=None,
                 faction_filter=None,
                 ):
        self.min_date = float(min_date)
        self.max_date = float(max_date)
        self.country_filter = int(country_filter) if country_filter is not None else country_filter
        self.type_filter = type_filter
        self.war_filter = int(war_filter) if war_filter is not None else war_filter
        self.leader_filter = int(leader_filter) if leader_filter is not None else leader_filter
        self.system_filter = int(system_filter) if system_filter is not None else system_filter
        self.planet_filter = int(planet_filter) if planet_filter is not None else planet_filter
        self.faction_filter = int(faction_filter) if faction_filter is not None else faction_filter

    def include_event(self, event: models.HistoricalEvent) -> bool:
        result = all([
            self.min_date <= event.start_date_days <= self.max_date,
            self.country_filter is None or self.country_filter == event.country_id,
            self.type_filter is None or self.type_filter == event.event_type,
        ])

        if self.war_filter is not None:
            result &= event.war_id == self.war_filter
        if self.leader_filter is not None:
            result &= event.leader_id == self.leader_filter
        if self.system_filter is not None:
            result &= event.system_id == self.system_filter
        if self.planet_filter is not None:
            result &= event.planet_id == self.planet_filter
        if self.faction_filter is not None:
            result &= event.faction_id == self.faction_filter
        return result


def get_event_and_link_dicts(
        session,
        game_id,
        event_filter: EventFilter = None,
):
    events = {}
    preformatted_links = {}
    country_details = {}

    key_objects = session.query(models.Country).order_by(models.Country.country_id.asc())
    for country_model in key_objects:
        events[country_model] = []
        preformatted_links[country_model] = preformat_history_url(
            country_model.country_name, game_id, country=country_model.country_id
        )

        country_details[country_model] = {
            "Country Type": game_info.convert_id_to_name(country_model.country_type),
        }
        gov = country_model.get_current_government()
        if gov is not None:
            country_details[country_model].update({
                "Personality": game_info.convert_id_to_name(gov.personality),
                "Government Type": game_info.convert_id_to_name(gov.gov_type, remove_prefix="gov"),
                "Authority": gov.authority,
                "Ethics": ", ".join([game_info.convert_id_to_name(e, remove_prefix="ethic") for e in sorted(gov.ethics)]),
                "Civics": ", ".join([game_info.convert_id_to_name(c, remove_prefix="civic") for c in sorted(gov.civics)]),
            })
        if not country_model.is_player:
            country_data = country_model.get_most_recent_data()
            if country_data:
                country_details[country_model]["Attitude"] = country_data.attitude_towards_player
                agreements = [
                    ("Research Agreement", country_data.has_research_agreement_with_player),
                    ("Sensor Link", country_data.has_sensor_link_with_player),
                    ("Rivalry", country_data.has_rivalry_with_player),
                    ("Defensive Pact", country_data.has_defensive_pact_with_player),
                    ("Migration Treaty", country_data.has_migration_treaty_with_player),
                    ("Federation", country_data.has_federation_with_player),
                    ("Non-aggression Pact", country_data.has_non_aggression_pact_with_player),
                    ("Closed Borders", country_data.has_closed_borders_with_player),
                ]
                country_details[country_model]["Diplomatic Status"] = ", ".join(a for (a, x) in agreements if x) or "None"
        else:
            country_details[country_model]["Attitude"] = "Player Country"
        event_list = session.query(models.HistoricalEvent).order_by(
            models.HistoricalEvent.start_date_days.asc()
        ).filter_by(country=country_model).all()

        for event in event_list:
            if event_filter and not event_filter.include_event(event):
                continue
            if not config.CONFIG.show_everything and not event.is_known_to_player:
                continue

            event_dict = dict(
                country=event.country,
                start_date=models.days_to_date(event.start_date_days),
                end_date=models.days_to_date(event.end_date_days),
                event_type=models.HISTORICAL_EVENT_TYPE_TO_STR_MAP[event.event_type],
                war=event.war,
                leader=event.leader,
                system=event.system,
                planet=event.planet,
                faction=event.faction,
                target_country=event.target_country,
                description=event.get_description(),
            )
            if event.planet and event_dict["system"] is None:
                event_dict["system"] = event.planet.system
            event_dict = {k: v for (k, v) in event_dict.items() if v is not None}
            events[country_model].append(
                event_dict
            )
            if event.leader:
                preformatted_links[event.leader] = preformat_history_url(event.leader.leader_name,
                                                                         game_id,
                                                                         leader=event.leader.leader_id)
            if event.system:
                preformatted_links[event.system] = preformat_history_url(game_info.convert_id_to_name(event.system.original_name, remove_prefix="NAME"),
                                                                         game_id,
                                                                         system=event.system.system_id)
            if event.target_country:
                preformatted_links[event.target_country] = preformat_history_url(event.target_country.country_name,
                                                                                 game_id,
                                                                                 country=event.target_country.country_id)
            if not config.CONFIG.allow_backdating and event.start_date_days < 0:
                event_dict["start_date"] = models.days_to_date(0)
                event_dict["end_date"] = models.days_to_date(0)
        if not events[country_model]:
            del events[country_model]
    return events, country_details, preformatted_links


def preformat_history_url(text, game_id, **kwargs):
    return f'<a class="textlink" href={flask.url_for("history_page", game_id=game_id, **kwargs)}>{text}</a>'


def get_war_dicts(session, current_date):
    wars = []
    for war in session.query(models.War).order_by(models.War.start_date_days).all():
        if not config.Config.show_everything:
            is_visible_to_player = False
            for wp in war.participants:
                if wp.country.first_player_contact_date is None:
                    continue
                else:
                    is_visible_to_player = True
                    break
            if not is_visible_to_player:
                continue

        start = models.days_to_date(war.start_date_days)
        end = models.days_to_date(current_date)
        if war.end_date_days:
            end = models.days_to_date(war.end_date_days)

        combats = sorted([combat for combat in war.combat], key=lambda combat: combat.date)
        wars.append(dict(
            war=war,
            start=start,
            end=end,
            attackers=[
                dict(country=wp.country, wp=wp)
                for wp in war.participants if wp.is_attacker
            ],
            defenders=[
                dict(country=wp.country, wp=wp)
                for wp in war.participants if not wp.is_attacker
            ],
            combat=[
                str(combat) for combat in combats
                if combat.attacker_war_exhaustion + combat.defender_war_exhaustion > 0.01
                   or combat.combat_type == models.CombatType.armies
            ],
        ))

    return wars


def get_country_color(country_name: str, alpha: float = 1.0) -> str:
    alpha = min(alpha, 1)
    alpha = max(alpha, 0)
    r, g, b = visualization_data.get_color_vals(country_name)
    r, g, b = r * 255, g * 255, b * 255
    color = f"rgba({r},{g},{b},{alpha})"
    return color


def get_most_recent_date(session):
    most_recent_gs = session.query(models.GameState).order_by(models.GameState.date.desc()).first()
    if most_recent_gs is None:
        most_recent_date = 0
    else:
        most_recent_date = most_recent_gs.date
    return most_recent_date


def get_figure_data(game_id: str, plot_spec: visualization_data.PlotSpecification):
    start = time.time()
    plot_data = visualization_data.get_current_execution_plot_data(game_id)
    plot_list = get_plot_lines(plot_data, plot_spec)
    end = time.time()
    logger.debug(f"Update took {end - start} seconds!")
    return plot_list


def start_server():
    timeline_app.run_server(port=config.CONFIG.port)


if __name__ == '__main__':
    start_server()
