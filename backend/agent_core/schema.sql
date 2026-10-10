-- Core learning-table schema recovered from read-only historical test schema. No data copied.
CREATE TABLE IF NOT EXISTS courses (
	id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	code VARCHAR(40) NOT NULL, 
	semester VARCHAR(40) NOT NULL, 
	start_date VARCHAR(20), 
	color VARCHAR(16) NOT NULL, 
	created_at DATETIME NOT NULL, 
	sync_uuid VARCHAR(36) NOT NULL, 
	updated_at DATETIME NOT NULL, teacher VARCHAR(120) NOT NULL DEFAULT '', location VARCHAR(200) NOT NULL DEFAULT '', slots TEXT NOT NULL DEFAULT '[]', 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS materials (
	id INTEGER NOT NULL, 
	course_id INTEGER, 
	title VARCHAR(300) NOT NULL, 
	type VARCHAR(40) NOT NULL, 
	file_path VARCHAR(600) NOT NULL, 
	file_name VARCHAR(300) NOT NULL, 
	file_hash VARCHAR(64) NOT NULL, 
	source VARCHAR(20) NOT NULL, 
	text TEXT NOT NULL, 
	tags JSON NOT NULL, 
	confidence FLOAT NOT NULL, 
	created_at DATETIME NOT NULL, 
	sync_uuid VARCHAR(36) NOT NULL, 
	updated_at DATETIME NOT NULL, chapter VARCHAR(120) NOT NULL DEFAULT '', difficulty VARCHAR(20) NOT NULL DEFAULT '', exam_focus BOOLEAN NOT NULL DEFAULT 0, 
	PRIMARY KEY (id), 
	FOREIGN KEY(course_id) REFERENCES courses (id)
);

CREATE TABLE IF NOT EXISTS tasks (
	id INTEGER NOT NULL, 
	course_id INTEGER, 
	title VARCHAR(300) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	due_at DATETIME NOT NULL, 
	done BOOLEAN NOT NULL, 
	remind_offsets_h JSON NOT NULL, 
	note TEXT NOT NULL, 
	source_text TEXT NOT NULL, 
	source_material_id INTEGER, 
	confidence FLOAT NOT NULL, 
	created_at DATETIME NOT NULL, 
	sync_uuid VARCHAR(36) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(course_id) REFERENCES courses (id), 
	FOREIGN KEY(source_material_id) REFERENCES materials (id)
);

CREATE TABLE IF NOT EXISTS review_plans (
	id INTEGER NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	exam_task_ids JSON NOT NULL, 
	sessions JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	sync_uuid VARCHAR(36) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS material_reviews (
	id INTEGER NOT NULL, 
	material_id INTEGER NOT NULL, 
	mastery FLOAT NOT NULL, 
	repetitions INTEGER NOT NULL, 
	lapses INTEGER NOT NULL, 
	interval_days FLOAT NOT NULL, 
	ease FLOAT NOT NULL, 
	due_at DATETIME, 
	last_reviewed_at DATETIME, 
	last_rating VARCHAR(10) NOT NULL, 
	history JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	sync_uuid VARCHAR(36) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(material_id) REFERENCES materials (id)
);

CREATE TABLE IF NOT EXISTS focus_sessions (
	id INTEGER NOT NULL, 
	mode VARCHAR(10) NOT NULL, 
	planned_min INTEGER NOT NULL, 
	duration_min FLOAT NOT NULL, 
	completed BOOLEAN NOT NULL, 
	interrupt_count INTEGER NOT NULL, 
	task_id INTEGER, 
	course_id INTEGER, 
	plan_id INTEGER, 
	plan_date VARCHAR(20) NOT NULL, 
	material_ids JSON NOT NULL, 
	note TEXT NOT NULL, 
	started_at DATETIME NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(task_id) REFERENCES tasks (id), 
	FOREIGN KEY(course_id) REFERENCES courses (id), 
	FOREIGN KEY(plan_id) REFERENCES review_plans (id)
);

CREATE TABLE IF NOT EXISTS push_deliveries (
	id INTEGER NOT NULL, 
	task_id INTEGER NOT NULL, 
	subscription_id INTEGER NOT NULL, 
	offset_h INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS tombstones (
	sync_uuid VARCHAR(60) NOT NULL, 
	entity VARCHAR(20) NOT NULL, 
	deleted_at DATETIME NOT NULL, 
	PRIMARY KEY (sync_uuid, entity)
);
