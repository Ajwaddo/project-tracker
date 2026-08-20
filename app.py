from __future__ import annotations

import json
from datetime import date, datetime, timedelta
import re

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from db import (
    Comment,
    LaneField,
    LaneSetting,
    Project,
    StatusHistory,
    Subtab,
    Task,
    TaskFieldValue,
    User,
    get_session,
    init_db,
    seed_data,
)

LANES = ["DA", "DS", "DE"]
STATUSES = ["Backlog", "In Progress", "Blocked", "Review", "Done"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]
FIELD_TYPES = ["text", "checkbox", "choice"]
FIELD_TYPE_LABELS = {"text": "Text", "checkbox": "Checkbox", "choice": "Single choice"}
BASE_TASK_COLUMNS = [
    "ID",
    "Title",
    "Description",
    "Sub-tab",
    "Status",
    "Priority",
    "Assignees",
    "Due Date",
    "Created At",
    "Updated At",
]
RENAMABLE_BASE_COLUMNS = [column for column in BASE_TASK_COLUMNS if column != "ID"]
RESERVED_FIELD_NAMES = {column.lower() for column in BASE_TASK_COLUMNS} | {lane.lower() for lane in LANES}
CUSTOM_FIELD_PREFIX = "field:"


def setup_page() -> None:
    st.set_page_config(page_title="Project Tracker", page_icon="PT", layout="wide")
    st.title("Project Tracker")
    st.caption("DA, DS, and DE lanes with subtabs, task tracking, comments, and progress review")


def ensure_db() -> None:
    init_db()
    seed_data()


def default_project(session) -> Project:
    project = session.query(Project).order_by(Project.id.asc()).first()
    if project is None:
        project = Project(name="Main Project", description="Prototype project", status="Active")
        session.add(project)
        session.commit()
        session.refresh(project)
    return project


def default_actor(session) -> User | None:
    return (
        session.query(User)
        .filter(User.active.is_(True))
        .order_by(User.id.asc())
        .first()
    )


def lane_users(session, lane: str) -> list[User]:
    return (
        session.query(User)
        .filter(User.role == lane, User.active.is_(True))
        .order_by(User.name.asc())
        .all()
    )


def lane_tasks(session, lane: str) -> list[Task]:
    return (
        session.query(Task)
        .options(
            selectinload(Task.assignees),
            selectinload(Task.field_values),
            selectinload(Task.comments).selectinload(Comment.author),
            selectinload(Task.status_history),
        )
        .filter(Task.lane == lane)
        .order_by(Task.updated_at.desc(), Task.created_at.desc())
        .all()
    )


def lane_subtabs(session, lane: str) -> list[str]:
    return [
        row[0]
        for row in session.query(Subtab.name)
        .filter(Subtab.lane == lane)
        .order_by(Subtab.name.asc())
        .all()
    ]


def lane_fields(session, lane: str) -> list[LaneField]:
    return (
        session.query(LaneField)
        .filter(LaneField.lane == lane)
        .order_by(LaneField.order_index.asc(), LaneField.created_at.asc(), LaneField.id.asc())
        .all()
    )


def lane_setting(session, lane: str) -> LaneSetting:
    setting = session.query(LaneSetting).filter(LaneSetting.lane == lane).first()
    if setting is None:
        setting = LaneSetting(
            lane=lane,
            subtabs_enabled=True,
            hidden_columns="[]",
            column_labels="{}",
            column_order="[]",
            trailing_hidden_count=0,
        )
        session.add(setting)
        session.commit()
        session.refresh(setting)
    if not setting.hidden_columns:
        setting.hidden_columns = "[]"
        session.commit()
    if not setting.column_labels:
        setting.column_labels = "{}"
        session.commit()
    if not setting.column_order:
        setting.column_order = "[]"
        session.commit()
    if setting.trailing_hidden_count is None:
        setting.trailing_hidden_count = 0
        session.commit()
    return setting


def parse_choice_options(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    options: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        option = item.strip()
        if option and option not in options:
            options.append(option)
    return options


def normalize_choice_options_text(raw_value: str) -> list[str]:
    parts = re.split(r"[\n,]+", raw_value)
    options: list[str] = []
    for part in parts:
        option = part.strip()
        if option and option not in options:
            options.append(option)
    return options


def save_choice_options(session, field: LaneField, options: list[str]) -> None:
    field.field_options = json.dumps(options)
    session.commit()


def field_choice_options(field: LaneField) -> list[str]:
    return parse_choice_options(getattr(field, "field_options", "[]"))


def sanitize_choice_field_values(session, field: LaneField, options: list[str]) -> None:
    if field.field_type != "choice":
        return

    valid = set(options)
    task_values = session.query(TaskFieldValue).filter(TaskFieldValue.field_id == field.id).all()
    for task_value in task_values:
        if task_value.value not in valid:
            session.delete(task_value)


def parse_hidden_columns(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def save_hidden_columns(session, setting: LaneSetting, hidden_columns: list[str]) -> None:
    setting.hidden_columns = json.dumps(hidden_columns)
    session.commit()


def parse_column_labels(raw_value: str | None) -> dict[str, str]:
    if not raw_value:
        return {}
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    labels: dict[str, str] = {}
    for column, label in value.items():
        if isinstance(column, str) and isinstance(label, str) and label.strip():
            labels[column] = label.strip()
    return labels


def save_column_labels(session, setting: LaneSetting, column_labels: dict[str, str]) -> None:
    setting.column_labels = json.dumps(column_labels)
    session.commit()


def lane_column_labels(setting: LaneSetting) -> dict[str, str]:
    labels = {column: column for column in BASE_TASK_COLUMNS}
    labels.update(
        {
            column: label
            for column, label in parse_column_labels(setting.column_labels).items()
            if column in RENAMABLE_BASE_COLUMNS and label.strip()
        }
    )
    return labels


def display_task_column_label(column: str, column_labels: dict[str, str]) -> str:
    return column_labels.get(column, column)


def lane_task_columns(fields: list[LaneField]) -> list[str]:
    return BASE_TASK_COLUMNS + [field.name for field in fields]


def visible_task_columns(all_columns: list[str], hidden_columns: list[str]) -> list[str]:
    hidden = set(hidden_columns)
    return [column for column in all_columns if column not in hidden]


def visibility_editor_rows(
    column_keys: list[str],
    hidden_columns: list[str],
    setting: LaneSetting,
    fields: list[LaneField],
) -> pd.DataFrame:
    hidden = set(hidden_columns)
    rows = [
        {"Column": lane_display_label(column_key, setting, fields), "Visible": column_key not in hidden}
        for column_key in column_keys
    ]
    return pd.DataFrame(rows, index=column_keys, columns=["Column", "Visible"])


def normalize_visibility_selection(
    all_columns: list[str],
    edited_df: pd.DataFrame,
) -> list[str] | None:
    visible = [str(index) for index, row in edited_df.iterrows() if bool(row["Visible"])]
    if not visible:
        st.warning("At least one column must remain visible.")
        return None

    return [column for column in all_columns if column not in visible]


def assignee_names(task: Task) -> str:
    return ", ".join(user.name for user in task.assignees) if task.assignees else "-"


def user_name_map(users: list[User]) -> dict[int, str]:
    return {user.id: user.name for user in users}


def is_truthy_field_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "checked"}


def format_field_value(field: LaneField, value: object) -> object:
    if field.field_type == "checkbox":
        return "Yes" if is_truthy_field_value(value) else "No"
    if field.field_type == "choice":
        options = field_choice_options(field)
        text_value = str(value).strip() if value is not None else ""
        return text_value if text_value in options else ""
    return value or ""


def widget_value_for_field(field: LaneField, value: object) -> object:
    if field.field_type == "checkbox":
        return is_truthy_field_value(value)
    if field.field_type == "choice":
        options = field_choice_options(field)
        text_value = str(value).strip() if value is not None else ""
        return text_value if text_value in options else ""
    return value or ""


def normalize_field_input(field: LaneField, value: object) -> str | None:
    if field.field_type == "checkbox":
        return "true" if is_truthy_field_value(value) else "false"
    if field.field_type == "choice":
        options = field_choice_options(field)
        text_value = str(value).strip()
        if not text_value or text_value not in options:
            return None
        return text_value
    text_value = str(value).strip()
    return text_value if text_value else None


def field_type_label(field_type: str) -> str:
    return FIELD_TYPE_LABELS.get(field_type, field_type)


def coerce_editor_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def safe_widget_key(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value)


def custom_field_key(field: LaneField) -> str:
    return f"{CUSTOM_FIELD_PREFIX}{field.id}"


def is_custom_field_key(column_key: str) -> bool:
    return column_key.startswith(CUSTOM_FIELD_PREFIX)


def custom_field_id(column_key: str) -> int | None:
    if not is_custom_field_key(column_key):
        return None
    try:
        return int(column_key[len(CUSTOM_FIELD_PREFIX) :])
    except ValueError:
        return None


def parse_column_order(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def save_column_order(session, setting: LaneSetting, column_order: list[str]) -> None:
    setting.column_order = json.dumps(column_order)
    session.commit()


def normalize_trailing_hidden_count(value: object) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(2, count))


def save_trailing_hidden_count(session, setting: LaneSetting, count: int) -> None:
    setting.trailing_hidden_count = normalize_trailing_hidden_count(count)
    session.commit()


def lane_column_field_map(fields: list[LaneField]) -> dict[str, LaneField]:
    return {custom_field_key(field): field for field in fields}


def lane_column_order(setting: LaneSetting, fields: list[LaneField]) -> list[str]:
    field_map = lane_column_field_map(fields)
    default_order = [column for column in RENAMABLE_BASE_COLUMNS] + [custom_field_key(field) for field in fields]
    available = set(default_order)
    stored = [column for column in parse_column_order(setting.column_order) if column in available]
    ordered: list[str] = []
    for column in stored + default_order:
        if column in available and column not in ordered:
            ordered.append(column)
    return ordered


def normalize_lane_column_order(session, setting: LaneSetting, fields: list[LaneField]) -> list[str]:
    stored = parse_column_order(setting.column_order)
    ordered = lane_column_order(setting, fields)
    if stored != ordered:
        save_column_order(session, setting, ordered)
    return ordered


def lane_column_key_lookup(setting: LaneSetting, fields: list[LaneField]) -> dict[str, str]:
    lookup = {column: column for column in BASE_TASK_COLUMNS}
    lookup.update({label: column for column, label in lane_column_labels(setting).items()})
    for field in fields:
        lookup[custom_field_key(field)] = custom_field_key(field)
        lookup[field.name] = custom_field_key(field)
    return lookup


def normalize_lane_hidden_columns(
    session,
    setting: LaneSetting,
    fields: list[LaneField],
    column_keys: list[str],
) -> list[str]:
    hidden = parse_hidden_columns(setting.hidden_columns)
    lookup = lane_column_key_lookup(setting, fields)
    normalized: list[str] = []
    changed = False
    for item in hidden:
        key = lookup.get(item, item)
        if key in column_keys and key not in normalized:
            normalized.append(key)
            if key != item:
                changed = True
    if changed or normalized != hidden:
        save_hidden_columns(session, setting, normalized)
    return normalized


def trailing_hidden_columns(column_keys: list[str], count: int) -> list[str]:
    if count <= 0:
        return []
    return column_keys[-count:]


def lane_effective_hidden_columns(
    session,
    setting: LaneSetting,
    fields: list[LaneField],
    column_keys: list[str],
) -> list[str]:
    explicit_hidden = normalize_lane_hidden_columns(session, setting, fields, column_keys)
    trailing_hidden = trailing_hidden_columns(column_keys, normalize_trailing_hidden_count(setting.trailing_hidden_count))
    return list(dict.fromkeys(explicit_hidden + trailing_hidden))


def compare_dataframe_records(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    def normalize(value: object) -> object:
        if pd.isna(value):
            return None
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float, str)):
            return value
        return str(value)

    left_records = [
        {key: normalize(value) for key, value in row.items()}
        for row in left.to_dict(orient="records")
    ]
    right_records = [
        {key: normalize(value) for key, value in row.items()}
        for row in right.to_dict(orient="records")
    ]
    return left_records == right_records


def lane_display_label(column_key: str, setting: LaneSetting, fields: list[LaneField]) -> str:
    if column_key in BASE_TASK_COLUMNS:
        return lane_column_labels(setting).get(column_key, column_key)
    field = lane_column_field_map(fields).get(column_key)
    return field.name if field is not None else column_key


def lane_header_names(
    setting: LaneSetting,
    fields: list[LaneField],
    *,
    exclude_base_column: str | None = None,
    exclude_field_id: int | None = None,
    proposed_base_labels: dict[str, str] | None = None,
) -> list[str]:
    labels = lane_column_labels(setting)
    if proposed_base_labels:
        labels.update(
            {
                column: label.strip()
                for column, label in proposed_base_labels.items()
                if column in BASE_TASK_COLUMNS and label.strip()
            }
        )

    headers = [
        labels[column]
        for column in BASE_TASK_COLUMNS
        if column != exclude_base_column
    ]
    headers.extend(
        field.name
        for field in fields
        if exclude_field_id is None or field.id != exclude_field_id
    )
    return headers


def lane_header_conflict(candidate: str, existing_headers: list[str]) -> bool:
    normalized = candidate.strip().lower()
    if not normalized:
        return True
    return normalized in {header.strip().lower() for header in existing_headers if header.strip()}


def normalize_due_date_value(value: object) -> date | None:
    if pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def task_rows(tasks: list[Task], fields: list[LaneField]) -> pd.DataFrame:
    rows = []
    for task in tasks:
        value_map = {value.field_id: value.value for value in task.field_values}
        row = {
            "ID": task.id,
            "Title": task.title,
            "Description": task.description,
            "Sub-tab": task.subtab_name or "-",
            "Status": task.status,
            "Priority": task.priority,
            "Assignees": assignee_names(task),
            "Due Date": task.due_date,
            "Created At": task.created_at.strftime("%Y-%m-%d %H:%M"),
            "Updated At": task.updated_at.strftime("%Y-%m-%d %H:%M"),
        }
        for field in fields:
            row[custom_field_key(field)] = format_field_value(field, value_map.get(field.id))
        rows.append(row)
    ordered_columns = BASE_TASK_COLUMNS + [custom_field_key(field) for field in fields]
    return pd.DataFrame(rows, columns=ordered_columns)


def task_editor_rows(tasks: list[Task], fields: list[LaneField], subtabs_enabled: bool) -> pd.DataFrame:
    rows = []
    for task in tasks:
        value_map = {value.field_id: value.value for value in task.field_values}
        row = {
            "ID": task.id,
            "Title": task.title,
            "Description": task.description,
            "Sub-tab": task.subtab_name or "",
            "Status": task.status,
            "Priority": task.priority,
            "Due Date": task.due_date,
            "Assignees": assignee_names(task),
            "Created At": task.created_at.strftime("%Y-%m-%d %H:%M"),
            "Updated At": task.updated_at.strftime("%Y-%m-%d %H:%M"),
        }
        for field in fields:
            field_key = custom_field_key(field)
            value = value_map.get(field.id)
            if field.field_type == "checkbox":
                row[field_key] = is_truthy_field_value(value)
            elif field.field_type == "choice":
                options = field_choice_options(field)
                text_value = str(value).strip() if value is not None else ""
                row[field_key] = text_value if text_value in options else ""
            else:
                row[field_key] = value or ""
        rows.append(row)

    ordered_columns = [
        "ID",
        "Title",
        "Description",
        "Sub-tab",
        "Status",
        "Priority",
        "Due Date",
        "Assignees",
        "Created At",
        "Updated At",
    ] + [custom_field_key(field) for field in fields]
    return pd.DataFrame(rows, columns=ordered_columns)


def task_field_column_configs(
    fields: list[LaneField],
    subtabs: list[str],
    subtabs_enabled: bool,
    column_labels: dict[str, str],
) -> dict[str, object]:
    configs: dict[str, object] = {
        "ID": st.column_config.TextColumn(display_task_column_label("ID", column_labels), disabled=True),
        "Title": st.column_config.TextColumn(display_task_column_label("Title", column_labels)),
        "Description": st.column_config.TextColumn(display_task_column_label("Description", column_labels)),
        "Sub-tab": st.column_config.SelectboxColumn(
            display_task_column_label("Sub-tab", column_labels),
            options=[""] + subtabs,
            required=False,
            help="Choose a lane sub-tab." if subtabs_enabled else "Sub-tabs are disabled for this lane.",
        ),
        "Status": st.column_config.SelectboxColumn(display_task_column_label("Status", column_labels), options=STATUSES),
        "Priority": st.column_config.SelectboxColumn(display_task_column_label("Priority", column_labels), options=PRIORITIES),
        "Due Date": st.column_config.DateColumn(display_task_column_label("Due Date", column_labels)),
        "Assignees": st.column_config.TextColumn(display_task_column_label("Assignees", column_labels)),
        "Created At": st.column_config.TextColumn(display_task_column_label("Created At", column_labels)),
        "Updated At": st.column_config.TextColumn(display_task_column_label("Updated At", column_labels)),
    }

    if not subtabs_enabled:
        configs["Sub-tab"] = st.column_config.TextColumn(display_task_column_label("Sub-tab", column_labels))

    for field in fields:
        field_key = custom_field_key(field)
        if field.field_type == "checkbox":
            configs[field_key] = st.column_config.CheckboxColumn(field.name)
        elif field.field_type == "choice":
            options = field_choice_options(field)
            configs[field_key] = (
                st.column_config.SelectboxColumn(field.name, options=[""] + options, required=False)
                if options
                else st.column_config.TextColumn(field.name)
            )
        else:
            configs[field_key] = st.column_config.TextColumn(field.name)
    return configs


def editable_task_columns(fields: list[LaneField], subtabs_enabled: bool) -> list[str]:
    columns = ["Title", "Description", "Status", "Priority", "Due Date"] + [custom_field_key(field) for field in fields]
    if subtabs_enabled:
        columns.insert(2, "Sub-tab")
    return columns


def column_visibility_panel(
    session,
    lane: str,
    setting: LaneSetting,
    column_keys: list[str],
    fields: list[LaneField],
) -> list[str]:
    visible_columns = [column for column in column_keys if column != "ID"]
    current_hidden = lane_effective_hidden_columns(session, setting, fields, visible_columns)
    visibility_df = visibility_editor_rows(visible_columns, current_hidden, setting, fields)

    st.subheader("Column visibility")
    st.caption("Show or hide columns for this lane. Changes are saved automatically.")
    edited_visibility_df = st.data_editor(
        visibility_df,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=f"column_visibility_{lane}",
        disabled=["Column"],
        column_config={
            "Column": st.column_config.TextColumn("Column"),
            "Visible": st.column_config.CheckboxColumn("Visible"),
        },
    )

    new_hidden = normalize_visibility_selection(visible_columns, edited_visibility_df)
    if new_hidden is not None and new_hidden != current_hidden:
        trailing_hidden = set(trailing_hidden_columns(visible_columns, normalize_trailing_hidden_count(setting.trailing_hidden_count)))
        explicit_hidden = [column for column in new_hidden if column not in trailing_hidden]
        save_hidden_columns(session, setting, explicit_hidden)
        st.rerun()

    return ["ID"] + [column for column in visible_columns if column not in current_hidden]


def apply_task_grid_changes(
    session,
    lane: str,
    tasks: list[Task],
    subtabs: list[str],
    fields: list[LaneField],
    visible_columns: list[str],
    subtabs_enabled: bool,
    edited_df: pd.DataFrame,
    actor: User | None,
) -> bool:
    if actor is None:
        st.info("Add at least one active team member before updating tasks.")
        return False

    task_map = {task.id: task for task in tasks}
    visible_fields = [field for field in fields if custom_field_key(field) in visible_columns]
    editable_columns = [column for column in editable_task_columns(visible_fields, subtabs_enabled) if column in visible_columns]
    if edited_df.empty:
        return False

    changes_applied = False

    try:
        for _, edited_row in edited_df.iterrows():
            task_id = int(edited_row["ID"])
            task = task_map.get(task_id)
            if task is None:
                continue

            title = coerce_editor_text(edited_row["Title"]).strip()
            description = coerce_editor_text(edited_row["Description"]).strip()
            new_status = coerce_editor_text(edited_row["Status"]).strip()
            new_priority = coerce_editor_text(edited_row["Priority"]).strip()
            new_due_date = normalize_due_date_value(edited_row["Due Date"])
            new_subtab = coerce_editor_text(edited_row["Sub-tab"]).strip() if subtabs_enabled else task.subtab_name

            original_values = {
                "Title": task.title,
                "Description": task.description,
                "Sub-tab": task.subtab_name or "",
                "Status": task.status,
                "Priority": task.priority,
                "Due Date": task.due_date,
            }
            edited_values = {
                "Title": title,
                "Description": description,
                "Sub-tab": new_subtab if subtabs_enabled else task.subtab_name or "",
                "Status": new_status,
                "Priority": new_priority,
                "Due Date": new_due_date,
            }
            for field in fields:
                field_key = custom_field_key(field)
                if field_key not in visible_columns:
                    continue
                value = next((fv.value for fv in task.field_values if fv.field_id == field.id), "")
                original_values[field_key] = is_truthy_field_value(value) if field.field_type == "checkbox" else (value or "")
                edited_values[field_key] = is_truthy_field_value(edited_row[field_key]) if field.field_type == "checkbox" else coerce_editor_text(edited_row[field_key]).strip()

            row_changed = any(edited_values[column] != original_values[column] for column in editable_columns if column in edited_values)
            if not row_changed:
                continue

            if not title:
                st.error(f"Task #{task.id} needs a title.")
                return False
            if new_status not in STATUSES:
                st.error(f"Task #{task.id} has an invalid status.")
                return False
            if new_priority not in PRIORITIES:
                st.error(f"Task #{task.id} has an invalid priority.")
                return False
            if subtabs_enabled and new_subtab and new_subtab not in subtabs:
                st.error(f"Task #{task.id} uses an unknown sub-tab.")
                return False

            old_status = task.status
            task.title = title
            task.description = description
            task.status = new_status
            task.priority = new_priority
            if subtabs_enabled:
                task.subtab_name = new_subtab
            task.due_date = new_due_date

            existing_values = {value.field_id: value for value in task.field_values}
            for field in visible_fields:
                field_key = custom_field_key(field)
                raw_value = edited_row[field_key]
                normalized_value = normalize_field_input(field, raw_value)
                existing_value = existing_values.get(field.id)
                if normalized_value is None:
                    if existing_value is not None:
                        session.delete(existing_value)
                    continue

                if existing_value is None:
                    session.add(TaskFieldValue(task_id=task.id, field_id=field.id, value=normalized_value))
                else:
                    existing_value.value = normalized_value

            if old_status != new_status:
                session.add(
                    StatusHistory(
                        task_id=task.id,
                        old_status=old_status,
                        new_status=new_status,
                        changed_by_id=actor.id,
                    )
                )

            changes_applied = True

        if changes_applied:
            session.commit()
        return changes_applied
    except Exception:
        session.rollback()
        raise


def create_subtab_form(session, lane: str, subtabs_enabled: bool) -> None:
    if not subtabs_enabled:
        st.info("Sub-tabs are turned off for this lane.")
        return

    with st.form(f"create_subtab_{lane}", clear_on_submit=True):
        new_name = st.text_input("New sub-tab name", placeholder="e.g. Cleanup, Modeling, QA")
        submitted = st.form_submit_button("Create sub-tab")
        if submitted:
            name = new_name.strip()
            if not name:
                st.error("Sub-tab name is required.")
                return

            exists = (
                session.query(Subtab)
                .filter(Subtab.lane == lane, Subtab.name == name)
                .first()
            )
            if exists:
                st.warning("That sub-tab already exists.")
                return

            session.add(Subtab(lane=lane, name=name))
            session.commit()
            st.success(f"Created {name}.")
            st.rerun()


def rename_subtab_form(session, lane: str, subtabs: list[str]) -> None:
    if not subtabs:
        st.info("Create a sub-tab first if you want to rename one.")
        return

    with st.expander("Edit sub-tab name"):
        with st.form(f"rename_subtab_{lane}", clear_on_submit=True):
            current_name = st.selectbox("Current sub-tab", subtabs)
            new_name = st.text_input("New sub-tab name", value=current_name)
            submitted = st.form_submit_button("Rename sub-tab")

            if submitted:
                old_name = current_name.strip()
                updated_name = new_name.strip()

                if not updated_name:
                    st.error("Sub-tab name is required.")
                    return
                if updated_name == old_name:
                    st.info("The new name matches the current name.")
                    return

                exists = (
                    session.query(Subtab)
                    .filter(Subtab.lane == lane, Subtab.name == updated_name)
                    .first()
                )
                if exists:
                    st.warning("That sub-tab name already exists.")
                    return

                subtab = (
                    session.query(Subtab)
                    .filter(Subtab.lane == lane, Subtab.name == old_name)
                    .first()
                )
                if subtab is None:
                    st.error("That sub-tab no longer exists.")
                    return

                subtab.name = updated_name
                session.query(Task).filter(Task.lane == lane, Task.subtab_name == old_name).update(
                    {Task.subtab_name: updated_name},
                    synchronize_session=False,
                )
                session.commit()
                st.success(f"Renamed {old_name} to {updated_name}.")
                st.rerun()


def next_lane_field_order(fields: list[LaneField]) -> int:
    return max((field.order_index for field in fields), default=-1) + 1


def move_lane_field(session, lane: str, field_id: int, direction: int) -> None:
    fields = lane_fields(session, lane)
    index_by_id = {field.id: idx for idx, field in enumerate(fields)}
    current_index = index_by_id.get(field_id)
    if current_index is None:
        st.error("That field no longer exists.")
        return

    target_index = current_index + direction
    if target_index < 0 or target_index >= len(fields):
        return

    current_field = fields[current_index]
    target_field = fields[target_index]
    current_order = current_field.order_index
    current_field.order_index = target_field.order_index
    target_field.order_index = current_order
    session.commit()
    st.rerun()


def move_lane_column(session, lane: str, setting: LaneSetting, fields: list[LaneField], column_key: str, direction: int) -> None:
    ordered_columns = lane_column_order(setting, fields)
    index_by_key = {key: idx for idx, key in enumerate(ordered_columns)}
    current_index = index_by_key.get(column_key)
    if current_index is None:
        return

    target_index = current_index + direction
    if target_index < 0 or target_index >= len(ordered_columns):
        return

    ordered_columns[current_index], ordered_columns[target_index] = ordered_columns[target_index], ordered_columns[current_index]
    save_column_order(session, setting, ordered_columns)
    st.rerun()


def apply_lane_field_update(
    session,
    lane: str,
    field: LaneField,
    new_name: str,
    new_type: str,
    new_options_text: str,
    fields: list[LaneField],
    setting: LaneSetting,
) -> None:
    old_name = field.name
    old_type = field.field_type
    name = new_name.strip()
    if not name:
        st.error("Field name is required.")
        return
    if name.lower() in RESERVED_FIELD_NAMES:
        st.error("That field name is reserved.")
        return
    if lane_header_conflict(
        name,
        lane_header_names(setting, fields, exclude_field_id=field.id),
    ):
        st.warning("That name already exists in this lane.")
        return

    normalized_type = new_type if new_type in FIELD_TYPES else "text"
    normalized_options = normalize_choice_options_text(new_options_text) if normalized_type == "choice" else []
    if normalized_type == "choice" and not normalized_options:
        st.error("Choice fields need at least one option.")
        return

    if field.field_type != normalized_type:
        field.field_type = normalized_type
        task_values = session.query(TaskFieldValue).filter(TaskFieldValue.field_id == field.id).all()
        if normalized_type == "checkbox":
            field.field_options = "[]"
            for task_value in task_values:
                task_value.value = "true" if is_truthy_field_value(task_value.value) else "false"
        elif normalized_type == "choice":
            save_choice_options(session, field, normalized_options)
            sanitize_choice_field_values(session, field, normalized_options)
        elif old_type == "checkbox":
            field.field_options = "[]"
            for task_value in task_values:
                task_value.value = "Yes" if is_truthy_field_value(task_value.value) else "No"
    elif normalized_type == "choice":
        save_choice_options(session, field, normalized_options)
        sanitize_choice_field_values(session, field, normalized_options)
    elif normalized_type != "choice":
        field.field_options = "[]"

    if old_name != name:
        field.name = name

    session.commit()
    st.rerun()


def lane_columns_settings_panel(session, lane: str, fields: list[LaneField], setting: LaneSetting) -> None:
    current_labels = lane_column_labels(setting)
    ordered_columns = normalize_lane_column_order(session, setting, fields)
    field_map = lane_column_field_map(fields)

    with st.expander("Columns and field names", expanded=True):
        st.caption("Rename and reorder built-in and custom columns from one place.")

        with st.form(f"add_lane_field_{lane}", clear_on_submit=True):
            row1, row2, row3 = st.columns([2.6, 1.0, 2.2])
            with row1:
                field_name = st.text_input("New custom field", placeholder="e.g. Story Points, Owner Note")
            with row2:
                field_type = st.selectbox(
                    "Type",
                    FIELD_TYPES,
                    format_func=field_type_label,
                    index=0,
                    label_visibility="visible",
                )
            with row3:
                field_options_text = st.text_input(
                    "Options",
                    placeholder="Pending, In Progress, Done",
                )
            submitted = st.form_submit_button("Add field")

            if submitted:
                name = field_name.strip()
                if not name:
                    st.error("Field name is required.")
                    return
                if name.lower() in RESERVED_FIELD_NAMES:
                    st.error("That field name is reserved.")
                    return
                if lane_header_conflict(name, lane_header_names(setting, fields)):
                    st.warning("That field already exists in this lane.")
                    return
                field_options = normalize_choice_options_text(field_options_text) if field_type == "choice" else []
                if field_type == "choice" and not field_options:
                    st.error("Choice fields need at least one option.")
                    return

                session.add(
                    LaneField(
                        lane=lane,
                        name=name,
                        field_type=field_type,
                        field_options=json.dumps(field_options),
                        order_index=next_lane_field_order(fields),
                    )
                )
                session.commit()
                st.success(f"Added custom field {name}.")
                st.rerun()

        st.divider()

        for idx, column_key in enumerate(ordered_columns):
            is_base_column = column_key in BASE_TASK_COLUMNS
            field = field_map.get(column_key)
            with st.container(border=False):
                move_left, move_right, body = st.columns([0.45, 0.45, 5.4])
                with move_left:
                    up_disabled = idx == 0
                    if st.button("↑", key=f"move_column_up_{lane}_{column_key}", disabled=up_disabled):
                        move_lane_column(session, lane, setting, fields, column_key, -1)
                with move_right:
                    down_disabled = idx == len(ordered_columns) - 1
                    if st.button("↓", key=f"move_column_down_{lane}_{column_key}", disabled=down_disabled):
                        move_lane_column(session, lane, setting, fields, column_key, 1)
                with body:
                    title_col, input_col, extra_col, options_col, action_col = st.columns([1.1, 2.2, 1.0, 2.0, 0.75])
                    with title_col:
                        st.markdown(f"**{lane_display_label(column_key, setting, fields)}**")
                        st.caption("Built-in" if is_base_column else "Custom")
                    with input_col:
                        if is_base_column:
                            new_name = st.text_input(
                                "Display name",
                                value=current_labels.get(column_key, column_key),
                                key=f"builtin_column_label_{lane}_{column_key}",
                                label_visibility="collapsed",
                            )
                        else:
                            if field is None:
                                st.caption("Field missing")
                                new_name = ""
                            else:
                                new_name = st.text_input(
                                    "Field name",
                                    value=field.name,
                                    key=f"custom_column_name_{lane}_{column_key}",
                                    label_visibility="collapsed",
                                )
                    with extra_col:
                        if is_base_column:
                            st.caption("Base column")
                        else:
                            new_type = st.selectbox(
                                "Type",
                                FIELD_TYPES,
                                index=FIELD_TYPES.index(field.field_type) if field and field.field_type in FIELD_TYPES else 0,
                                format_func=field_type_label,
                                key=f"custom_column_type_{lane}_{column_key}",
                                label_visibility="collapsed",
                            )
                    with options_col:
                        if is_base_column:
                            st.caption("Display only")
                            new_options_text = ""
                        else:
                            if field is not None and new_type == "choice":
                                new_options_text = st.text_input(
                                    "Options",
                                    value=", ".join(field_choice_options(field)),
                                    key=f"custom_column_options_{lane}_{column_key}",
                                    label_visibility="collapsed",
                                )
                            else:
                                new_options_text = ""
                                st.caption("Not used")
                    with action_col:
                        if st.button("Save", key=f"save_column_{lane}_{column_key}"):
                            if is_base_column:
                                proposed_labels = current_labels.copy()
                                proposed_labels[column_key] = new_name.strip()
                                if not proposed_labels[column_key]:
                                    st.error("Display name is required.")
                                    return
                                conflict_headers = lane_header_names(
                                    setting,
                                    fields,
                                    proposed_base_labels=proposed_labels,
                                )
                                if len({header.strip().lower() for header in conflict_headers}) != len(conflict_headers):
                                    st.error("Built-in column names must be unique across the lane.")
                                    return
                                save_column_labels(
                                    session,
                                    setting,
                                    {k: v for k, v in proposed_labels.items() if v.strip() and k in RENAMABLE_BASE_COLUMNS},
                                )
                                st.success("Built-in column name updated.")
                                st.rerun()
                            else:
                                if field is None:
                                    st.error("That field no longer exists.")
                                    return
                                apply_lane_field_update(
                                    session,
                                    lane,
                                    field,
                                    new_name,
                                    new_type,
                                    new_options_text,
                                    fields,
                                    setting,
                                )


def create_task_form(
    session,
    lane: str,
    project: Project,
    users: list[User],
    subtabs: list[str],
    fields: list[LaneField],
    subtabs_enabled: bool,
    actor: User | None,
    column_labels: dict[str, str],
) -> None:
    with st.expander(f"Create {lane} task", expanded=False):
        if actor is None:
            st.info("Add at least one active team member before creating tasks.")
            return

        with st.form(f"create_task_{lane}", clear_on_submit=True):
            title = st.text_input(display_task_column_label("Title", column_labels))
            description = st.text_area(display_task_column_label("Description", column_labels))
            subtab_name = ""
            if subtabs_enabled:
                subtab_name = st.selectbox(display_task_column_label("Sub-tab", column_labels), [""] + subtabs)
            status = st.selectbox(display_task_column_label("Status", column_labels), STATUSES, index=0)
            priority = st.selectbox(display_task_column_label("Priority", column_labels), PRIORITIES, index=1)
            due_date = st.date_input(display_task_column_label("Due Date", column_labels), value=date.today())
            field_inputs = {
                field.id: (
                    st.checkbox(field.name, value=False, key=f"create_field_{lane}_{field.id}")
                    if field.field_type == "checkbox"
                    else (
                        st.selectbox(
                            field.name,
                            [""] + field_choice_options(field),
                            key=f"create_field_{lane}_{field.id}",
                        )
                        if field.field_type == "choice" and field_choice_options(field)
                        else st.text_input(field.name, key=f"create_field_{lane}_{field.id}")
                    )
                )
                for field in fields
            }

            user_names = user_name_map(users)
            assignee_ids = st.multiselect(
                display_task_column_label("Assignees", column_labels),
                [user.id for user in users],
                format_func=lambda user_id: user_names.get(user_id, str(user_id)),
            )
            submitted = st.form_submit_button("Create task")

            if submitted:
                if not title.strip():
                    st.error("Task title is required.")
                    return

                task = Task(
                    project_id=project.id,
                    lane=lane,
                    subtab_name=subtab_name,
                    title=title.strip(),
                    description=description.strip(),
                    status=status,
                    priority=priority,
                    due_date=due_date,
                    created_by_id=actor.id,
                )
                task.assignees = session.query(User).filter(User.id.in_(assignee_ids)).all() if assignee_ids else []
                session.add(task)
                session.flush()
                for field in fields:
                    value = normalize_field_input(field, field_inputs[field.id])
                    if value is not None:
                        session.add(TaskFieldValue(task_id=task.id, field_id=field.id, value=value))
                session.add(
                    StatusHistory(
                        task_id=task.id,
                        old_status="Backlog",
                        new_status=status,
                        changed_by_id=actor.id,
                    )
                )
                session.commit()
                st.success("Task created.")
                st.rerun()


def render_task_grid(
    session,
    lane: str,
    tasks: list[Task],
    subtabs: list[str],
    fields: list[LaneField],
    visible_columns: list[str],
    subtabs_enabled: bool,
    column_labels: dict[str, str],
    subtab_name: str,
    actor: User | None,
) -> None:
    subset = tasks if subtab_name == "All" else [task for task in tasks if (task.subtab_name or "") == subtab_name]
    if not subset:
        st.info("No tasks in this sub-tab yet.")
        return

    if not visible_columns:
        st.warning("No columns are visible for this lane. Use the column visibility table above to show at least one column.")
        return

    visible_fields = [field for field in fields if custom_field_key(field) in visible_columns]
    grid_df = task_editor_rows(subset, visible_fields, subtabs_enabled)
    grid_df = grid_df[visible_columns]
    disabled_columns = ["Assignees", "Created At", "Updated At"]
    if not subtabs_enabled:
        disabled_columns.append("Sub-tab")

    editor_key = f"task_grid_{lane}_{safe_widget_key(subtab_name)}_{safe_widget_key('__'.join(visible_columns))}"
    edited_df = st.data_editor(
        grid_df,
        use_container_width=True,
        hide_index=False,
        num_rows="fixed",
        key=editor_key,
        disabled=disabled_columns if actor is not None else True,
        column_config=task_field_column_configs(visible_fields, subtabs, subtabs_enabled, column_labels),
    )
    table_changed = compare_dataframe_records(grid_df, edited_df)

    if actor is None:
        st.info("Add at least one active team member before editing tasks.")
        return

    if st.button("Save table changes", key=f"save_{editor_key}", type="primary", disabled=not table_changed):
        changed = apply_task_grid_changes(
            session=session,
            lane=lane,
            tasks=subset,
            subtabs=subtabs,
            fields=visible_fields,
            visible_columns=visible_columns,
            subtabs_enabled=subtabs_enabled,
            edited_df=edited_df,
            actor=actor,
        )
        if changed:
            st.session_state.pop(editor_key, None)
            st.success("Task table updated.")
            st.rerun()
        else:
            st.info("No table changes to save.")


def task_actions_panel(
    session,
    lane: str,
    users: list[User],
    tasks: list[Task],
    actor: User | None,
    column_labels: dict[str, str],
) -> None:
    if not tasks:
        st.info("No tasks yet in this lane.")
        return
    if actor is None:
        st.info("Add at least one active team member before updating tasks.")
        return

    st.divider()
    st.subheader(display_task_column_label("Assignees", column_labels) + " and comments")
    task_titles = {task.id: task.title for task in tasks}
    task_key = f"task_action_choice_{lane}"
    if st.session_state.get(task_key) not in task_titles:
        st.session_state[task_key] = next(iter(task_titles.keys()))
    task_id = st.selectbox(
        f"Select {lane} task",
        list(task_titles.keys()),
        format_func=lambda selected_id: f"#{selected_id} - {task_titles.get(selected_id, '')}",
        key=task_key,
    )
    task_choice = next(task for task in tasks if task.id == task_id)

    user_names = user_name_map(users)
    with st.form(f"task_actions_{lane}_{task_choice.id}"):
        new_assignee_ids = st.multiselect(
            display_task_column_label("Assignees", column_labels),
            [user.id for user in users],
            default=[user.id for user in task_choice.assignees],
            format_func=lambda user_id: user_names.get(user_id, str(user_id)),
        )
        note = st.text_area("Comment / update")
        submitted = st.form_submit_button("Save changes")

        if submitted:
            task_choice.assignees = session.query(User).filter(User.id.in_(new_assignee_ids)).all() if new_assignee_ids else []
            if note.strip():
                session.add(
                    Comment(
                        task_id=task_choice.id,
                        user_id=actor.id,
                        comment_text=note.strip(),
                    )
                )
            session.commit()
            st.success("Task actions updated.")
            st.rerun()

    st.subheader("Comments")
    comments = (
        session.query(Comment)
        .filter(Comment.task_id == task_choice.id)
        .order_by(Comment.created_at.desc())
        .all()
    )
    if not comments:
        st.caption("No comments yet.")
    for comment in comments:
        st.write(f"**{comment.author.name}** at {comment.created_at.strftime('%Y-%m-%d %H:%M')}")
        st.write(comment.comment_text)
        st.caption("---")

    st.divider()
    with st.expander("Delete task", expanded=False):
        st.warning("This permanently deletes the task, its comments, and its status history.")
        confirm_delete = st.checkbox(
            "I understand this cannot be undone",
            key=f"confirm_delete_{lane}_{task_choice.id}",
        )
        if st.button("Delete task", type="primary", disabled=not confirm_delete, key=f"delete_task_{lane}_{task_choice.id}"):
            task_choice.assignees.clear()
            session.delete(task_choice)
            session.commit()
            st.success("Task deleted.")
            st.rerun()


def lane_tasks_page(
    session,
    lane: str,
    project: Project,
    users: list[User],
    tasks: list[Task],
    subtabs: list[str],
    fields: list[LaneField],
    setting: LaneSetting,
    actor: User | None,
) -> None:
    column_labels = lane_column_labels(setting)
    ordered_columns = normalize_lane_column_order(session, setting, fields)
    hidden_columns = lane_effective_hidden_columns(session, setting, fields, ordered_columns)
    visible_columns = visible_task_columns(["ID"] + ordered_columns, hidden_columns)

    c1, c2, c3 = st.columns(3)
    c1.metric("Tasks", len(tasks))
    c2.metric("Blocked", sum(task.status == "Blocked" for task in tasks))
    c3.metric("Done", sum(task.status == "Done" for task in tasks))

    create_task_form(session, lane, project, users, subtabs, fields, setting.subtabs_enabled, actor, column_labels)

    st.divider()
    st.subheader("Tasks")
    if not setting.subtabs_enabled:
        st.info("Sub-tabs are disabled for this lane. Existing tasks still keep their saved sub-tab names.")
        render_task_grid(session, lane, tasks, subtabs, fields, visible_columns, setting.subtabs_enabled, column_labels, "All", actor)
    else:
        available_tabs = ["All"] + subtabs
        if len(available_tabs) == 1:
            st.info("No subtabs yet. Create one in Settings.")
        else:
            for tab_widget, tab_name in zip(st.tabs(available_tabs), available_tabs):
                with tab_widget:
                    render_task_grid(session, lane, tasks, subtabs, fields, visible_columns, setting.subtabs_enabled, column_labels, tab_name, actor)

    task_actions_panel(session, lane, users, tasks, actor, column_labels)


def lane_settings_page(
    session,
    lane: str,
    subtabs: list[str],
    fields: list[LaneField],
    setting: LaneSetting,
) -> None:
    column_labels = lane_column_labels(setting)
    ordered_columns = normalize_lane_column_order(session, setting, fields)
    trailing_hidden_count = normalize_trailing_hidden_count(setting.trailing_hidden_count)
    st.subheader(f"{lane} Lane Settings")
    st.caption("All lane-scoped settings are saved in SQLite and restored when you reopen the lane.")

    current_subtabs_enabled = st.checkbox(
        "Enable sub-tabs for this lane",
        value=setting.subtabs_enabled,
        key=f"subtabs_enabled_{lane}",
    )
    if current_subtabs_enabled != setting.subtabs_enabled:
        setting.subtabs_enabled = current_subtabs_enabled
        session.commit()
        st.rerun()

    top_left, top_right = st.columns([1, 1])
    with top_left:
        st.subheader("Sub-tabs")
        if setting.subtabs_enabled:
            st.info("Use subtabs to group work inside each lane.")
        else:
            st.info("Sub-tabs are turned off for this lane.")
        create_subtab_form(session, lane, setting.subtabs_enabled)
        rename_subtab_form(session, lane, subtabs)

    with top_right:
        column_visibility_panel(session, lane, setting, ["ID"] + ordered_columns, fields)
        trailing_choice = st.selectbox(
            "Hide columns from right edge",
            [0, 1, 2],
            index=[0, 1, 2].index(trailing_hidden_count),
            help="Hide the last 0, 1, or 2 visible columns on this lane.",
            key=f"trailing_hidden_count_{lane}",
        )
        if trailing_choice != trailing_hidden_count:
            save_trailing_hidden_count(session, setting, trailing_choice)
            st.rerun()

    st.divider()
    lane_columns_settings_panel(session, lane, fields, setting)

    st.divider()
    st.caption("Field order, type, and display-name updates are saved here for this lane.")


def lane_panel(session, lane: str) -> None:
    project = default_project(session)
    users = lane_users(session, lane)
    tasks = lane_tasks(session, lane)
    subtabs = lane_subtabs(session, lane)
    fields = lane_fields(session, lane)
    setting = lane_setting(session, lane)
    actor = default_actor(session)

    st.subheader(f"{lane} Lane")
    st.caption(f"Current project: {project.name}")

    page = st.radio(
        "Lane page",
        ["Tasks", "Settings"],
        horizontal=True,
        key=f"lane_page_{lane}",
    )

    if page == "Tasks":
        lane_tasks_page(session, lane, project, users, tasks, subtabs, fields, setting, actor)
    else:
        lane_settings_page(session, lane, subtabs, fields, setting)


def dashboard(session) -> None:
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    tasks = session.query(Task).all()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total tasks", len(tasks))
    c2.metric("Open tasks", sum(task.status != "Done" for task in tasks))
    c3.metric("Blocked tasks", sum(task.status == "Blocked" for task in tasks))
    c4.metric(
        "Overdue",
        sum(task.due_date is not None and task.due_date < today and task.status != "Done" for task in tasks),
    )

    st.metric(
        "Completed this week",
        sum(
            history.new_status == "Done" and history.changed_at.date() >= start_of_week
            for task in tasks
            for history in task.status_history
        ),
    )

    status_df = pd.DataFrame(
        [{"Status": status, "Count": sum(task.status == status for task in tasks)} for status in STATUSES]
    )
    status_df = status_df[status_df["Count"] > 0]

    left, right = st.columns(2)
    with left:
        st.subheader("Task Status")
        if not status_df.empty:
            fig = px.bar(status_df, x="Status", y="Count", color="Status", text="Count")
            fig.update_layout(showlegend=False, height=320, margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No tasks yet.")

    with right:
        st.subheader("Workload by Lane")
        lane_df = pd.DataFrame([{"Lane": lane, "Count": sum(task.lane == lane for task in tasks)} for lane in LANES])
        fig = px.bar(lane_df, x="Lane", y="Count", color="Lane", text="Count")
        fig.update_layout(showlegend=False, height=320, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Overdue Tasks")
    overdue = [
        {
            "Lane": task.lane,
            "Title": task.title,
            "Sub-tab": task.subtab_name or "-",
            "Status": task.status,
            "Due Date": task.due_date,
            "Assignees": assignee_names(task),
        }
        for task in tasks
        if task.due_date is not None and task.due_date < today and task.status != "Done"
    ]
    if overdue:
        st.dataframe(pd.DataFrame(overdue), use_container_width=True, hide_index=True)
    else:
        st.info("No overdue tasks.")


def team_management(session) -> None:
    st.subheader("Team Management")
    users = session.query(User).order_by(User.role.asc(), User.name.asc()).all()
    rows = [
        {
            "Name": user.name,
            "Email": user.email,
            "Role": user.role,
            "Active": user.active,
            "Assigned Tasks": len(user.assigned_tasks),
        }
        for user in users
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No team members yet.")

    with st.expander("Add team member"):
        with st.form("add_user", clear_on_submit=True):
            name = st.text_input("Name")
            email = st.text_input("Email")
            role = st.selectbox("Role", LANES)
            active = st.checkbox("Active", value=True)
            submitted = st.form_submit_button("Add user")
            if submitted:
                if not name.strip() or not email.strip():
                    st.error("Name and email are required.")
                else:
                    try:
                        session.add(
                            User(
                                name=name.strip(),
                                email=email.strip().lower(),
                                role=role,
                                active=active,
                            )
                        )
                        session.commit()
                        st.success("User added.")
                        st.rerun()
                    except IntegrityError:
                        session.rollback()
                        st.error("That email already exists.")


def main() -> None:
    setup_page()
    ensure_db()
    session = get_session()
    try:
        st.sidebar.write("Navigation")
        page = st.sidebar.radio("Go to", ["Dashboard", "DA", "DS", "DE", "Team"], label_visibility="collapsed")
        st.sidebar.divider()
        st.sidebar.write("Database")
        st.sidebar.code("project_tracker.db")
        st.sidebar.write(f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        if page == "Dashboard":
            dashboard(session)
        elif page == "Team":
            team_management(session)
        else:
            lane_panel(session, page)
    finally:
        session.close()


if __name__ == "__main__":
    main()
