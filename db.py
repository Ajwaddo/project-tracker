from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "project_tracker.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()
DEFAULT_SUBTAB_NAMES = {"Backlog", "In Progress", "Review", "Done"}


task_assignments = Table(
    "task_assignments",
    Base.metadata,
    Column("task_id", ForeignKey("tasks.id"), primary_key=True),
    Column("user_id", ForeignKey("users.id"), primary_key=True),
    Column("assigned_at", DateTime, default=datetime.utcnow, nullable=False),
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(200), unique=True, nullable=False)
    role = Column(String(20), nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    assigned_tasks = relationship("Task", secondary=task_assignments, back_populates="assignees")
    comments = relationship("Comment", back_populates="author")
    status_changes = relationship("StatusHistory", back_populates="changer")


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    description = Column(Text, default="", nullable=False)
    status = Column(String(30), default="Active", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan")


class LaneSetting(Base):
    __tablename__ = "lane_settings"

    id = Column(Integer, primary_key=True)
    lane = Column(String(10), nullable=False, unique=True)
    subtabs_enabled = Column(Boolean, default=True, nullable=False)
    hidden_columns = Column(Text, default="[]", nullable=False)
    column_labels = Column(Text, default="{}", nullable=False)
    column_order = Column(Text, default="[]", nullable=False)
    trailing_hidden_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Subtab(Base):
    __tablename__ = "subtabs"

    id = Column(Integer, primary_key=True)
    lane = Column(String(10), nullable=False)
    name = Column(String(120), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("lane", "name", name="uq_subtabs_lane_name"),)


class LaneField(Base):
    __tablename__ = "lane_fields"

    id = Column(Integer, primary_key=True)
    lane = Column(String(10), nullable=False)
    name = Column(String(120), nullable=False)
    field_type = Column(String(20), default="text", nullable=False)
    field_options = Column(Text, default="[]", nullable=False)
    order_index = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("lane", "name", name="uq_lane_fields_lane_name"),)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    lane = Column(String(10), default="DA", nullable=False)
    subtab_name = Column(String(120), default="", nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="", nullable=False)
    status = Column(String(30), default="Backlog", nullable=False)
    priority = Column(String(20), default="Medium", nullable=False)
    due_date = Column(Date, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="tasks")
    creator = relationship("User", foreign_keys=[created_by_id])
    assignees = relationship("User", secondary=task_assignments, back_populates="assigned_tasks")
    comments = relationship("Comment", back_populates="task", cascade="all, delete-orphan")
    status_history = relationship("StatusHistory", back_populates="task", cascade="all, delete-orphan")
    field_values = relationship("TaskFieldValue", back_populates="task", cascade="all, delete-orphan")


class TaskFieldValue(Base):
    __tablename__ = "task_field_values"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    field_id = Column(Integer, ForeignKey("lane_fields.id"), nullable=False)
    value = Column(Text, default="", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("Task", back_populates="field_values")
    field = relationship("LaneField")


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    comment_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("Task", back_populates="comments")
    author = relationship("User", back_populates="comments")


class StatusHistory(Base):
    __tablename__ = "status_history"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    old_status = Column(String(30), nullable=True)
    new_status = Column(String(30), nullable=False)
    changed_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("Task", back_populates="status_history")
    changer = relationship("User", back_populates="status_changes")


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_tasks_table()
    _migrate_lane_fields_table()
    _migrate_lane_settings_table()
    session = SessionLocal()
    try:
        ensure_default_lane_settings(session)
        remove_default_subtabs(session)
    finally:
        session.close()


def _migrate_tasks_table() -> None:
    inspector = inspect(engine)
    if "tasks" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("tasks")}
    statements = []
    if "lane" not in columns:
        statements.append("ALTER TABLE tasks ADD COLUMN lane VARCHAR(10) DEFAULT 'DA' NOT NULL")
    if "subtab_name" not in columns:
        statements.append("ALTER TABLE tasks ADD COLUMN subtab_name VARCHAR(120) DEFAULT '' NOT NULL")

    if statements:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
            conn.execute(text("UPDATE tasks SET lane = 'DA' WHERE lane IS NULL OR lane = ''"))
            conn.execute(text("UPDATE tasks SET subtab_name = '' WHERE subtab_name IS NULL"))


def _migrate_lane_fields_table() -> None:
    inspector = inspect(engine)
    if "lane_fields" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("lane_fields")}
    if "order_index" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE lane_fields ADD COLUMN order_index INTEGER DEFAULT 0 NOT NULL"))
    if "field_options" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE lane_fields ADD COLUMN field_options TEXT DEFAULT '[]' NOT NULL"))

    session = SessionLocal()
    try:
        lanes = [row[0] for row in session.query(LaneField.lane).distinct().order_by(LaneField.lane.asc()).all()]
        for lane in lanes:
            fields = (
                session.query(LaneField)
                .filter(LaneField.lane == lane)
                .order_by(LaneField.created_at.asc(), LaneField.id.asc())
                .all()
            )
            for index, field in enumerate(fields):
                field.order_index = index
        session.commit()
    finally:
        session.close()


def _migrate_lane_settings_table() -> None:
    inspector = inspect(engine)
    if "lane_settings" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("lane_settings")}
    statements = []
    if "hidden_columns" not in columns:
        statements.append("ALTER TABLE lane_settings ADD COLUMN hidden_columns TEXT DEFAULT '[]' NOT NULL")
    if "column_labels" not in columns:
        statements.append("ALTER TABLE lane_settings ADD COLUMN column_labels TEXT DEFAULT '{}' NOT NULL")
    if "column_order" not in columns:
        statements.append("ALTER TABLE lane_settings ADD COLUMN column_order TEXT DEFAULT '[]' NOT NULL")
    if "trailing_hidden_count" not in columns:
        statements.append("ALTER TABLE lane_settings ADD COLUMN trailing_hidden_count INTEGER DEFAULT 0 NOT NULL")

    if statements:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))


def ensure_default_project(session) -> Project:
    project = session.query(Project).order_by(Project.id.asc()).first()
    if project is None:
        project = Project(
            name="Main Project",
            description="Prototype project for DA, DS, and DE work.",
            status="Active",
        )
        session.add(project)
        session.commit()
        session.refresh(project)
    return project


def remove_default_subtabs(session) -> None:
    subtabs = (
        session.query(Subtab)
        .filter(Subtab.name.in_(DEFAULT_SUBTAB_NAMES))
        .all()
    )
    if subtabs:
        for subtab in subtabs:
            session.delete(subtab)

    tasks = (
        session.query(Task)
        .filter(Task.subtab_name.in_(DEFAULT_SUBTAB_NAMES))
        .all()
    )
    for task in tasks:
        task.subtab_name = ""

    session.commit()


def ensure_default_lane_settings(session) -> None:
    existing = {
        row[0]
        for row in session.query(LaneSetting.lane)
        .order_by(LaneSetting.lane.asc())
        .all()
    }
    for lane in ["DA", "DS", "DE"]:
        if lane not in existing:
            session.add(LaneSetting(lane=lane, subtabs_enabled=True))
    session.commit()


def seed_data() -> None:
    session = SessionLocal()
    try:
        users_exist = session.query(User).count() > 0
        if not users_exist:
            users = [
                User(name="Aina", email="aina@example.com", role="DA"),
                User(name="Daniel", email="daniel@example.com", role="DS"),
                User(name="Farah", email="farah@example.com", role="DE"),
            ]
            session.add_all(users)
            session.commit()

        project = ensure_default_project(session)
        remove_default_subtabs(session)

        if session.query(Task).count() == 0:
            users = {user.role: user for user in session.query(User).all()}
            tasks = [
                Task(
                    project_id=project.id,
                    lane="DA",
                    subtab_name="",
                    title="Prepare analysis dataset",
                    description="Collect, clean, and structure source data.",
                    status="In Progress",
                    priority="High",
                    due_date=date.today(),
                    created_by_id=users.get("DA").id if users.get("DA") else None,
                ),
                Task(
                    project_id=project.id,
                    lane="DS",
                    subtab_name="",
                    title="Train baseline model",
                    description="Build the first predictive model and evaluate it.",
                    status="Blocked",
                    priority="Critical",
                    due_date=date.today(),
                    created_by_id=users.get("DS").id if users.get("DS") else None,
                ),
                Task(
                    project_id=project.id,
                    lane="DE",
                    subtab_name="",
                    title="Set up ingestion pipeline",
                    description="Create the pipeline that feeds the dashboard.",
                    status="Backlog",
                    priority="Medium",
                    due_date=date.today(),
                    created_by_id=users.get("DE").id if users.get("DE") else None,
                ),
            ]
            for task in tasks:
                if task.lane == "DA" and users.get("DA"):
                    task.assignees.append(users["DA"])
                if task.lane == "DS" and users.get("DS"):
                    task.assignees.append(users["DS"])
                if task.lane == "DE" and users.get("DE"):
                    task.assignees.append(users["DE"])
            session.add_all(tasks)
            session.flush()
            session.add_all(
                [
                    StatusHistory(task_id=tasks[0].id, old_status="Backlog", new_status="In Progress", changed_by_id=tasks[0].created_by_id or 1),
                    StatusHistory(task_id=tasks[1].id, old_status="Backlog", new_status="Blocked", changed_by_id=tasks[1].created_by_id or 1),
                    Comment(task_id=tasks[0].id, user_id=tasks[0].created_by_id or 1, comment_text="Working on the clean dataset."),
                    Comment(task_id=tasks[1].id, user_id=tasks[1].created_by_id or 1, comment_text="Waiting on source approval."),
                ]
            )
            session.commit()
    finally:
        session.close()


def get_session():
    return SessionLocal()
