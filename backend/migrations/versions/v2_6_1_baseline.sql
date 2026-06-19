--
-- PostgreSQL database dump
--


-- Dumped from database version 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- *not* creating schema, since initdb creates it


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS '';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_action_provenance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_action_provenance (
    id character varying(36) NOT NULL,
    session_id character varying(64) NOT NULL,
    request_id character varying(64),
    iteration integer,
    tool_name character varying(128) NOT NULL,
    params_snapshot json,
    approval_scope character varying(32),
    approved boolean,
    outcome_success boolean,
    outcome_preview text,
    created_at timestamp without time zone
);


--
-- Name: agent_memories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_memories (
    id character varying(36) NOT NULL,
    content text NOT NULL,
    source character varying(50),
    session_id character varying(36),
    tags text,
    type character varying(50),
    importance double precision,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    project_id integer,
    user_id character varying(80),
    workspace_root character varying(1024),
    lesson_id character varying(36),
    confidence double precision DEFAULT 1.0,
    status character varying(32) DEFAULT 'active'::character varying,
    access_count integer DEFAULT 0,
    last_accessed_at timestamp without time zone,
    metadata jsonb
);


--
-- Name: agent_memory_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_memory_audit (
    id character varying(36) NOT NULL,
    memory_id character varying(36),
    action character varying(32) NOT NULL,
    actor character varying(80),
    before json,
    after json,
    created_at timestamp without time zone
);


--
-- Name: batch_job_columns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.batch_job_columns (
    id integer NOT NULL,
    job_id text NOT NULL,
    columns jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: batch_job_columns_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.batch_job_columns_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: batch_job_columns_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.batch_job_columns_id_seq OWNED BY public.batch_job_columns.id;


--
-- Name: batch_job_rows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.batch_job_rows (
    id integer NOT NULL,
    job_id text NOT NULL,
    row_data jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: batch_job_rows_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.batch_job_rows_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: batch_job_rows_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.batch_job_rows_id_seq OWNED BY public.batch_job_rows.id;


--
-- Name: clients; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.clients (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    email character varying(255),
    phone character varying(50),
    description text,
    logo_path character varying(255),
    notes text,
    contact_url character varying(500),
    location character varying(255),
    primary_service character varying(255),
    secondary_service character varying(255),
    brand_tone character varying(50),
    business_hours text,
    social_links text,
    industry character varying(100),
    target_audience text,
    unique_selling_points text,
    competitor_urls text,
    brand_voice_examples text,
    keywords text,
    content_goals text,
    regulatory_constraints text,
    geographic_coverage character varying(100),
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: clients_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.clients_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: clients_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.clients_id_seq OWNED BY public.clients.id;


--
-- Name: demo_steps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.demo_steps (
    id integer NOT NULL,
    demonstration_id integer NOT NULL,
    step_index integer NOT NULL,
    action_type character varying(20) NOT NULL,
    target_description text NOT NULL,
    element_context text,
    coordinates_x integer,
    coordinates_y integer,
    text text,
    keys character varying(255),
    intent text,
    precondition text,
    variability boolean NOT NULL,
    wait_condition text,
    is_mistake boolean NOT NULL,
    screenshot_before character varying(1024),
    screenshot_after character varying(1024),
    CONSTRAINT ck_demostep_action_type CHECK (((action_type)::text = ANY (ARRAY[('click'::character varying)::text, ('type'::character varying)::text, ('hotkey'::character varying)::text, ('scroll'::character varying)::text])))
);


--
-- Name: demo_steps_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.demo_steps_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: demo_steps_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.demo_steps_id_seq OWNED BY public.demo_steps.id;


--
-- Name: demonstrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.demonstrations (
    id integer NOT NULL,
    name character varying(255),
    description text NOT NULL,
    context_url character varying(1024),
    context_app character varying(255),
    tags json,
    autonomy_level character varying(20) NOT NULL,
    success_count integer NOT NULL,
    attempt_count integer NOT NULL,
    parent_demonstration_id integer,
    is_complete boolean NOT NULL,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    CONSTRAINT ck_demo_autonomy_level CHECK (((autonomy_level)::text = ANY (ARRAY[('guided'::character varying)::text, ('supervised'::character varying)::text, ('autonomous'::character varying)::text])))
);


--
-- Name: demonstrations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.demonstrations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: demonstrations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.demonstrations_id_seq OWNED BY public.demonstrations.id;


--
-- Name: device_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.device_profiles (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    device_type character varying(50),
    gpu_vram_mb integer,
    system_ram_mb integer,
    max_batch_size integer,
    max_seq_length integer,
    supports_4bit boolean,
    requires_cpu_offload boolean,
    is_default boolean,
    is_active boolean,
    compute_capability character varying(10),
    created_at timestamp without time zone
);


--
-- Name: device_profiles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.device_profiles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: device_profiles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.device_profiles_id_seq OWNED BY public.device_profiles.id;


--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id integer NOT NULL,
    filename character varying(255) NOT NULL,
    path character varying(1024) NOT NULL,
    type character varying(50),
    index_status character varying(50),
    indexed_at timestamp without time zone,
    error_message text,
    content text,
    is_code_file boolean,
    size integer,
    file_metadata text,
    content_category character varying(100),
    relevance_score double precision,
    summary text,
    rag_context text,
    folder_id integer,
    client_id integer,
    project_id integer,
    website_id integer,
    tags text,
    notes text,
    indexing_job_id character varying(255),
    uploaded_at timestamp without time zone,
    updated_at timestamp without time zone,
    source_document_id integer
);


--
-- Name: documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.documents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.documents_id_seq OWNED BY public.documents.id;


--
-- Name: eval_pairs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eval_pairs (
    id character varying(36) NOT NULL,
    eval_generation_id character varying(50),
    question text NOT NULL,
    expected_answer text NOT NULL,
    source_doc_id integer,
    source_chunk_hash character varying(64),
    corpus_type character varying(20),
    quality_score double precision,
    created_at timestamp without time zone
);


--
-- Name: experiment_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experiment_runs (
    id character varying(36) NOT NULL,
    run_tag character varying(100),
    phase integer NOT NULL,
    parameter_changed character varying(200) NOT NULL,
    old_value character varying(500),
    new_value character varying(500) NOT NULL,
    hypothesis text,
    composite_score double precision NOT NULL,
    baseline_score double precision,
    delta double precision,
    status character varying(20) NOT NULL,
    eval_details json,
    duration_seconds double precision,
    node_id character varying(36),
    created_at timestamp without time zone
);


--
-- Name: folders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.folders (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    path character varying(1024) NOT NULL,
    parent_id integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    is_repository boolean NOT NULL,
    description text,
    repo_metadata text,
    client_id integer,
    project_id integer,
    website_id integer,
    tags text,
    notes text
);


--
-- Name: folders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.folders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: folders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.folders_id_seq OWNED BY public.folders.id;


--
-- Name: generations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.generations (
    id character varying(36) NOT NULL,
    site_key character varying(255),
    delimiter character varying(10) NOT NULL,
    structured_html boolean NOT NULL,
    brand_tone character varying(50),
    meta_json text,
    created_at timestamp without time zone NOT NULL,
    client character varying(255),
    project character varying(255),
    website character varying(500),
    competitor character varying(500)
);


--
-- Name: google_indexing_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.google_indexing_config (
    id integer NOT NULL,
    website_id integer NOT NULL,
    enabled boolean NOT NULL,
    daily_cap integer NOT NULL,
    notification_type character varying(20) NOT NULL,
    last_sitemap_sync timestamp without time zone,
    last_run_at timestamp without time zone,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: google_indexing_config_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.google_indexing_config_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: google_indexing_config_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.google_indexing_config_id_seq OWNED BY public.google_indexing_config.id;


--
-- Name: google_indexing_submissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.google_indexing_submissions (
    id integer NOT NULL,
    website_id integer NOT NULL,
    url character varying(2048) NOT NULL,
    notification_type character varying(20) NOT NULL,
    status character varying(20) NOT NULL,
    http_status integer,
    error text,
    created_at timestamp without time zone,
    submitted_at timestamp without time zone
);


--
-- Name: google_indexing_submissions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.google_indexing_submissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: google_indexing_submissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.google_indexing_submissions_id_seq OWNED BY public.google_indexing_submissions.id;


--
-- Name: images; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.images (
    id character varying(36) NOT NULL,
    hash character varying(64) NOT NULL,
    file_name character varying(255) NOT NULL,
    file_path character varying(1024),
    file_size integer,
    mime_type character varying(100),
    tags text,
    created_at timestamp without time zone NOT NULL,
    last_used_at timestamp without time zone
);


--
-- Name: interconnector_broadcast_targets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interconnector_broadcast_targets (
    id integer NOT NULL,
    broadcast_id character varying(36),
    node_id character varying(36),
    status character varying(50),
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    items_pushed integer,
    error_message text,
    retry_count integer,
    approval_status character varying(50)
);


--
-- Name: interconnector_broadcast_targets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.interconnector_broadcast_targets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: interconnector_broadcast_targets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.interconnector_broadcast_targets_id_seq OWNED BY public.interconnector_broadcast_targets.id;


--
-- Name: interconnector_broadcasts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interconnector_broadcasts (
    id character varying(36) NOT NULL,
    sync_type character varying(20) NOT NULL,
    entities text,
    file_paths text,
    require_approval boolean,
    priority character varying(20),
    status character varying(50),
    initiated_at timestamp without time zone,
    scheduled_for timestamp without time zone,
    completed_at timestamp without time zone,
    total_clients integer,
    successful_count integer,
    failed_count integer,
    pending_count integer
);


--
-- Name: interconnector_conflicts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interconnector_conflicts (
    id integer NOT NULL,
    node_id character varying(36) NOT NULL,
    entity_type character varying(100) NOT NULL,
    entity_id character varying(255) NOT NULL,
    local_data text,
    remote_data text,
    conflict_fields text,
    resolution_strategy character varying(50),
    resolved boolean NOT NULL,
    resolved_at timestamp without time zone,
    resolved_by character varying(255),
    created_at timestamp without time zone
);


--
-- Name: interconnector_conflicts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.interconnector_conflicts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: interconnector_conflicts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.interconnector_conflicts_id_seq OWNED BY public.interconnector_conflicts.id;


--
-- Name: interconnector_learnings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interconnector_learnings (
    id integer NOT NULL,
    source_node_id character varying(36) NOT NULL,
    "timestamp" timestamp without time zone NOT NULL,
    learning_type character varying(50) NOT NULL,
    description text NOT NULL,
    code_diff text,
    confidence double precision,
    model_used character varying(100),
    applied_by text,
    uncle_reviewed boolean,
    uncle_feedback text
);


--
-- Name: interconnector_learnings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.interconnector_learnings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: interconnector_learnings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.interconnector_learnings_id_seq OWNED BY public.interconnector_learnings.id;


--
-- Name: interconnector_nodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interconnector_nodes (
    node_id character varying(36) NOT NULL,
    node_name character varying(255) NOT NULL,
    host character varying(255) NOT NULL,
    port integer NOT NULL,
    node_mode character varying(50) NOT NULL,
    status character varying(50) NOT NULL,
    last_heartbeat timestamp without time zone,
    sync_entities text,
    registered_at timestamp without time zone,
    last_sync_time timestamp without time zone,
    model_name character varying(100),
    vram_total integer,
    vram_free integer,
    specialties text DEFAULT '[]'::text,
    current_load double precision DEFAULT '0'::double precision,
    hardware_profile text DEFAULT '{}'::text,
    online boolean DEFAULT true NOT NULL
);


--
-- Name: interconnector_pending_approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interconnector_pending_approvals (
    id integer NOT NULL,
    push_id character varying(36) NOT NULL,
    source_node character varying(36) NOT NULL,
    sync_type character varying(20) NOT NULL,
    files_data text,
    entities_data text,
    received_at timestamp without time zone,
    reviewed_at timestamp without time zone,
    status character varying(50),
    decision_reason text,
    approved_files text,
    approved_entities text,
    auto_applied boolean
);


--
-- Name: interconnector_pending_approvals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.interconnector_pending_approvals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: interconnector_pending_approvals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.interconnector_pending_approvals_id_seq OWNED BY public.interconnector_pending_approvals.id;


--
-- Name: interconnector_pending_changes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interconnector_pending_changes (
    id integer NOT NULL,
    change_type character varying(50) NOT NULL,
    entity_type character varying(100) NOT NULL,
    entity_id character varying(255) NOT NULL,
    entity_data text,
    queued_at timestamp without time zone,
    retry_count integer NOT NULL,
    last_retry_at timestamp without time zone,
    status character varying(50) NOT NULL
);


--
-- Name: interconnector_pending_changes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.interconnector_pending_changes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: interconnector_pending_changes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.interconnector_pending_changes_id_seq OWNED BY public.interconnector_pending_changes.id;


--
-- Name: interconnector_sync_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interconnector_sync_history (
    id integer NOT NULL,
    node_id character varying(36) NOT NULL,
    sync_direction character varying(50) NOT NULL,
    entities_synced text,
    items_processed integer NOT NULL,
    items_created integer NOT NULL,
    items_updated integer NOT NULL,
    conflicts_resolved integer NOT NULL,
    sync_duration_ms integer,
    status character varying(50) NOT NULL,
    error_message text,
    sync_timestamp timestamp without time zone
);


--
-- Name: interconnector_sync_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.interconnector_sync_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: interconnector_sync_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.interconnector_sync_history_id_seq OWNED BY public.interconnector_sync_history.id;


--
-- Name: interconnector_sync_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interconnector_sync_profiles (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    profile_type character varying(50),
    entity_config text,
    file_config text,
    is_default boolean,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: interconnector_sync_profiles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.interconnector_sync_profiles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: interconnector_sync_profiles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.interconnector_sync_profiles_id_seq OWNED BY public.interconnector_sync_profiles.id;


--
-- Name: job_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.job_history (
    id character varying(255) NOT NULL,
    kind character varying(64) NOT NULL,
    native_id character varying(255) NOT NULL,
    label text NOT NULL,
    status character varying(32) NOT NULL,
    progress double precision,
    started_at timestamp without time zone,
    finished_at timestamp without time zone NOT NULL,
    duration_s double precision,
    error_message text,
    parent_id character varying(255),
    job_metadata json,
    recorded_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: llm_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_messages (
    id integer NOT NULL,
    session_id character varying(36) NOT NULL,
    role character varying(10) NOT NULL,
    content text NOT NULL,
    extra_data json,
    "timestamp" timestamp without time zone,
    project_id integer,
    CONSTRAINT ck_message_role CHECK (((role)::text = ANY (ARRAY[('user'::character varying)::text, ('assistant'::character varying)::text, ('system'::character varying)::text])))
);


--
-- Name: llm_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_messages_id_seq OWNED BY public.llm_messages.id;


--
-- Name: llm_session_summaries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_session_summaries (
    id integer NOT NULL,
    session_id character varying(36) NOT NULL,
    start_message_id integer,
    end_message_id integer,
    summary text NOT NULL,
    message_count integer,
    created_at timestamp without time zone
);


--
-- Name: llm_session_summaries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_session_summaries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_session_summaries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_session_summaries_id_seq OWNED BY public.llm_session_summaries.id;


--
-- Name: llm_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_sessions (
    id character varying(36) NOT NULL,
    "user" character varying(80) NOT NULL,
    project_id integer,
    created_at timestamp without time zone,
    mode character varying(20) DEFAULT 'chat'::character varying NOT NULL
);


--
-- Name: models; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.models (
    id integer NOT NULL,
    name character varying(80) NOT NULL,
    version character varying(80),
    quantized boolean,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: models_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.models_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: models_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.models_id_seq OWNED BY public.models.id;


--
-- Name: music_videos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.music_videos (
    id integer NOT NULL,
    project_id integer,
    name character varying(255) NOT NULL,
    song_document_id integer,
    song_path character varying(512),
    style_prompt text NOT NULL,
    status character varying(64) NOT NULL,
    current_stage character varying(64) NOT NULL,
    cut_plan json,
    clips json,
    output_document_id integer,
    settings_json json NOT NULL,
    error_blob json,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: music_videos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.music_videos_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: music_videos_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.music_videos_id_seq OWNED BY public.music_videos.id;


--
-- Name: pages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pages (
    id character varying(36) NOT NULL,
    generation_id character varying(36) NOT NULL,
    title text NOT NULL,
    slug character varying(500) NOT NULL,
    category text,
    tags text,
    content text NOT NULL,
    excerpt text,
    meta_json text,
    created_at timestamp without time zone NOT NULL,
    status character varying(20) NOT NULL,
    approved_at timestamp without time zone,
    deleted_at timestamp without time zone
);


--
-- Name: pending_fixes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pending_fixes (
    id integer NOT NULL,
    run_id integer,
    file_path character varying(1024) NOT NULL,
    original_content text,
    proposed_new_content text,
    proposed_diff text NOT NULL,
    fix_description text,
    severity character varying(20) DEFAULT 'medium'::character varying,
    status character varying(20) DEFAULT 'proposed'::character varying,
    reviewed_by character varying(50),
    review_notes text,
    created_at timestamp without time zone,
    reviewed_at timestamp without time zone,
    applied_at timestamp without time zone
);


--
-- Name: pending_fixes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pending_fixes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pending_fixes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pending_fixes_id_seq OWNED BY public.pending_fixes.id;


--
-- Name: production_shot_subjects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.production_shot_subjects (
    id integer NOT NULL,
    shot_id integer NOT NULL,
    subject_id integer NOT NULL
);


--
-- Name: production_shot_subjects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.production_shot_subjects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: production_shot_subjects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.production_shot_subjects_id_seq OWNED BY public.production_shot_subjects.id;


--
-- Name: production_shots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.production_shots (
    id integer NOT NULL,
    production_id integer NOT NULL,
    scene_number integer NOT NULL,
    shot_number integer NOT NULL,
    description text NOT NULL,
    camera_angle character varying(128),
    duration_seconds double precision NOT NULL,
    dialogue_text text,
    voice_subject_id integer,
    storyboard_image_path character varying(512),
    video_clip_path character varying(512),
    approved boolean NOT NULL,
    regen_count integer NOT NULL,
    scene_mood character varying(64),
    character_name character varying(255)
);


--
-- Name: production_shots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.production_shots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: production_shots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.production_shots_id_seq OWNED BY public.production_shots.id;


--
-- Name: production_subjects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.production_subjects (
    id integer NOT NULL,
    production_id integer NOT NULL,
    subject_id integer NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: production_subjects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.production_subjects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: production_subjects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.production_subjects_id_seq OWNED BY public.production_subjects.id;


--
-- Name: productions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.productions (
    id integer NOT NULL,
    project_id integer,
    name character varying(255) NOT NULL,
    script_text text NOT NULL,
    status character varying(64) NOT NULL,
    current_stage character varying(64) NOT NULL,
    settings_json json NOT NULL,
    error_blob json,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: productions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.productions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: productions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.productions_id_seq OWNED BY public.productions.id;


--
-- Name: project_rules_association; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_rules_association (
    project_id integer NOT NULL,
    rule_id integer NOT NULL
);


--
-- Name: projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.projects (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    client_id integer,
    project_type character varying(100),
    target_keywords text,
    content_strategy text,
    deliverables text,
    seo_strategy text,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: projects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.projects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: projects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.projects_id_seq OWNED BY public.projects.id;


--
-- Name: research_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.research_configs (
    id character varying(36) NOT NULL,
    params json NOT NULL,
    composite_score double precision,
    is_active boolean,
    promoted_at timestamp without time zone,
    source character varying(30),
    created_at timestamp without time zone
);


--
-- Name: retention_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.retention_audit (
    id bigint NOT NULL,
    occurred_at timestamp without time zone DEFAULT now() NOT NULL,
    actor character varying(32) NOT NULL,
    kind character varying(64) NOT NULL,
    operation character varying(64) NOT NULL,
    item_count integer NOT NULL,
    bytes_freed bigint,
    parameters json,
    triggered_by character varying(255)
);


--
-- Name: retention_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.retention_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: retention_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.retention_audit_id_seq OWNED BY public.retention_audit.id;


--
-- Name: rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rules (
    id integer NOT NULL,
    name character varying(100),
    level character varying(50) NOT NULL,
    type character varying(50),
    command_label character varying(100),
    reference_id character varying(255),
    rule_text text NOT NULL,
    description text,
    output_schema_name character varying(100),
    target_models text,
    is_active boolean NOT NULL,
    project_id integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    CONSTRAINT ck_rule_level_updated CHECK (((level)::text = ANY (ARRAY[('SYSTEM'::character varying)::text, ('PROJECT'::character varying)::text, ('CLIENT'::character varying)::text, ('USER_GLOBAL'::character varying)::text, ('USER_SPECIFIC'::character varying)::text, ('PROMPT'::character varying)::text, ('LEARNED'::character varying)::text]))),
    CONSTRAINT ck_rule_text_length_extended CHECK ((length(rule_text) <= 50000)),
    CONSTRAINT ck_rule_type CHECK (((type)::text = ANY (ARRAY[('PROMPT_TEMPLATE'::character varying)::text, ('QA_TEMPLATE'::character varying)::text, ('COMMAND_RULE'::character varying)::text, ('FILTER_RULE'::character varying)::text, ('FORMATTING_RULE'::character varying)::text, ('SYSTEM_PROMPT'::character varying)::text, ('OTHER'::character varying)::text])))
);


--
-- Name: rules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rules_id_seq OWNED BY public.rules.id;


--
-- Name: self_improvement_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.self_improvement_runs (
    id integer NOT NULL,
    "timestamp" timestamp without time zone NOT NULL,
    node_id character varying(36),
    trigger character varying(50) NOT NULL,
    status character varying(50),
    test_results_before text,
    test_results_after text,
    changes_made text,
    uncle_reviewed boolean,
    uncle_feedback text,
    learning_id integer,
    error_message text,
    duration_seconds double precision
);


--
-- Name: self_improvement_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.self_improvement_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: self_improvement_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.self_improvement_runs_id_seq OWNED BY public.self_improvement_runs.id;


--
-- Name: settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.settings (
    key text NOT NULL,
    value text,
    updated_at timestamp without time zone
);


--
-- Name: social_outreach_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.social_outreach_log (
    id integer NOT NULL,
    created_at timestamp without time zone,
    platform character varying(40) NOT NULL,
    action character varying(40) NOT NULL,
    target_url character varying(2048),
    target_thread_id character varying(255),
    draft_text text,
    posted_text text,
    status character varying(40) NOT NULL,
    grade_score double precision,
    abort_reason character varying(512),
    task_id integer
);


--
-- Name: social_outreach_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.social_outreach_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: social_outreach_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.social_outreach_log_id_seq OWNED BY public.social_outreach_log.id;


--
-- Name: subjects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subjects (
    id integer NOT NULL,
    kind character varying(32) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    ref_image_paths json NOT NULL,
    lora_path character varying(512),
    lora_version integer NOT NULL,
    training_status character varying(32) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    voice_id character varying(128),
    trigger_word character varying(64),
    cast_required boolean
);


--
-- Name: subjects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.subjects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: subjects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.subjects_id_seq OWNED BY public.subjects.id;


--
-- Name: swarm_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.swarm_messages (
    id integer NOT NULL,
    production_id integer NOT NULL,
    agent_name character varying(64) NOT NULL,
    input_json json NOT NULL,
    output_json json,
    latency_ms integer,
    model character varying(128),
    tokens_in integer,
    tokens_out integer,
    status character varying(32) NOT NULL,
    error_text text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: swarm_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.swarm_messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: swarm_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.swarm_messages_id_seq OWNED BY public.swarm_messages.id;


--
-- Name: symbol_hits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.symbol_hits (
    symbol_id character varying(128) NOT NULL,
    symbol_kind character varying(16),
    display_name character varying(255),
    module character varying(255),
    mode_flags integer,
    hit_count bigint,
    last_fired_at timestamp without time zone,
    static_reachability boolean
);


--
-- Name: system_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_settings (
    key character varying(100) NOT NULL,
    value text,
    updated_at timestamp without time zone
);


--
-- Name: tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tasks (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    status character varying(50),
    priority integer,
    due_date timestamp without time zone,
    type character varying(100),
    job_id character varying(36),
    output_filename character varying(255),
    prompt_text text,
    model_name character varying(120),
    workflow_config text,
    client_name character varying(255),
    target_website character varying(2048),
    competitor_url character varying(2048),
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    project_id integer,
    client_id integer,
    website_id integer,
    schedule_type character varying(50),
    cron_expression character varying(100),
    next_run_at timestamp without time zone,
    last_run_at timestamp without time zone,
    parent_task_id integer,
    retry_count integer,
    max_retries integer,
    retry_delay integer,
    error_message text,
    task_handler character varying(100),
    handler_config json,
    progress integer,
    result text
);


--
-- Name: tasks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tasks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tasks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tasks_id_seq OWNED BY public.tasks.id;


--
-- Name: tool_feedback; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tool_feedback (
    id integer NOT NULL,
    session_id character varying(36),
    tool_name character varying(100) NOT NULL,
    task text,
    positive boolean NOT NULL,
    steps integer,
    time_seconds double precision,
    model character varying(100),
    created_at timestamp without time zone,
    lesson_id character varying(36)
);


--
-- Name: tool_feedback_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tool_feedback_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tool_feedback_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tool_feedback_id_seq OWNED BY public.tool_feedback.id;


--
-- Name: training_datasets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.training_datasets (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    path character varying(1024),
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: training_datasets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.training_datasets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: training_datasets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.training_datasets_id_seq OWNED BY public.training_datasets.id;


--
-- Name: training_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.training_jobs (
    id integer NOT NULL,
    job_id character varying(64),
    name character varying(255),
    base_model character varying(255),
    output_model_name character varying(255),
    dataset_id integer,
    config_json text,
    device_profile_id integer,
    pipeline_stage character varying(50),
    status character varying(50),
    progress integer,
    current_step integer,
    total_steps integer,
    error_message text,
    metrics_json text,
    lora_path character varying(1024),
    gguf_path character varying(1024),
    ollama_model_name character varying(255),
    quantization_level character varying(50),
    created_at timestamp without time zone,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    celery_task_id character varying(64),
    checkpoint_path character varying(1024),
    pid integer,
    is_resumable boolean
);


--
-- Name: training_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.training_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: training_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.training_jobs_id_seq OWNED BY public.training_jobs.id;


--
-- Name: website_pages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.website_pages (
    id integer NOT NULL,
    website_id integer NOT NULL,
    url character varying(2048) NOT NULL,
    title text,
    content text,
    slug character varying(500),
    meta_description text,
    meta_keywords text,
    featured_image character varying(2048),
    og_metadata text,
    last_modified_sitemap timestamp without time zone,
    status character varying(50) NOT NULL,
    error_message text,
    crawled_at timestamp without time zone,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: website_pages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.website_pages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: website_pages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.website_pages_id_seq OWNED BY public.website_pages.id;


--
-- Name: websites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.websites (
    id integer NOT NULL,
    url character varying(2048) NOT NULL,
    sitemap character varying(2048),
    competitor_url character varying(2048),
    status character varying(50),
    last_crawled timestamp without time zone,
    project_id integer,
    client_id integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    local_path character varying(2048)
);


--
-- Name: websites_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.websites_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: websites_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.websites_id_seq OWNED BY public.websites.id;


--
-- Name: wordpress_pages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wordpress_pages (
    id integer NOT NULL,
    wordpress_site_id integer NOT NULL,
    wordpress_post_id integer NOT NULL,
    post_type character varying(50) NOT NULL,
    title text NOT NULL,
    content text,
    excerpt text,
    slug character varying(500),
    status character varying(50) NOT NULL,
    date timestamp without time zone,
    modified timestamp without time zone,
    author_id integer,
    author_name character varying(255),
    categories text,
    tags text,
    featured_image_url character varying(2048),
    featured_image_id integer,
    meta_data text,
    sitemap_priority double precision,
    sitemap_changefreq character varying(50),
    seo_title text,
    seo_description text,
    focus_keywords text,
    robots_meta text,
    canonical_url character varying(2048),
    schema_markup text,
    seo_plugin character varying(50),
    seo_score integer,
    page_score integer,
    seo_score_breakdown text,
    analytics_data text,
    pagespeed_score_mobile integer,
    pagespeed_score_desktop integer,
    pagespeed_data text,
    image_seo_data text,
    seo_score_history text,
    analytics_synced_at timestamp without time zone,
    pagespeed_synced_at timestamp without time zone,
    pull_status character varying(50) NOT NULL,
    pulled_at timestamp without time zone,
    original_content_hash character varying(64),
    process_status character varying(50) NOT NULL,
    improved_title text,
    improved_content text,
    improved_excerpt text,
    improved_meta_description text,
    improved_meta_title text,
    improved_schema text,
    improvement_summary text,
    processed_at timestamp without time zone,
    review_status character varying(50),
    reviewed_by character varying(255),
    reviewed_at timestamp without time zone,
    review_notes text,
    push_status character varying(50),
    pushed_at timestamp without time zone,
    push_error text,
    wordpress_response text,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: wordpress_pages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wordpress_pages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wordpress_pages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wordpress_pages_id_seq OWNED BY public.wordpress_pages.id;


--
-- Name: wordpress_sites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wordpress_sites (
    id integer NOT NULL,
    url character varying(2048) NOT NULL,
    site_name character varying(255),
    username character varying(255),
    api_key text NOT NULL,
    connection_type character varying(50) NOT NULL,
    client_id integer,
    project_id integer,
    website_id integer,
    pull_settings text,
    push_settings text,
    status character varying(50) NOT NULL,
    last_pull_at timestamp without time zone,
    last_push_at timestamp without time zone,
    last_test_at timestamp without time zone,
    error_message text,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: wordpress_sites_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wordpress_sites_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wordpress_sites_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wordpress_sites_id_seq OWNED BY public.wordpress_sites.id;


--
-- Name: batch_job_columns id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batch_job_columns ALTER COLUMN id SET DEFAULT nextval('public.batch_job_columns_id_seq'::regclass);


--
-- Name: batch_job_rows id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batch_job_rows ALTER COLUMN id SET DEFAULT nextval('public.batch_job_rows_id_seq'::regclass);


--
-- Name: clients id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clients ALTER COLUMN id SET DEFAULT nextval('public.clients_id_seq'::regclass);


--
-- Name: demo_steps id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.demo_steps ALTER COLUMN id SET DEFAULT nextval('public.demo_steps_id_seq'::regclass);


--
-- Name: demonstrations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.demonstrations ALTER COLUMN id SET DEFAULT nextval('public.demonstrations_id_seq'::regclass);


--
-- Name: device_profiles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_profiles ALTER COLUMN id SET DEFAULT nextval('public.device_profiles_id_seq'::regclass);


--
-- Name: documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents ALTER COLUMN id SET DEFAULT nextval('public.documents_id_seq'::regclass);


--
-- Name: folders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders ALTER COLUMN id SET DEFAULT nextval('public.folders_id_seq'::regclass);


--
-- Name: google_indexing_config id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_indexing_config ALTER COLUMN id SET DEFAULT nextval('public.google_indexing_config_id_seq'::regclass);


--
-- Name: google_indexing_submissions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_indexing_submissions ALTER COLUMN id SET DEFAULT nextval('public.google_indexing_submissions_id_seq'::regclass);


--
-- Name: interconnector_broadcast_targets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_broadcast_targets ALTER COLUMN id SET DEFAULT nextval('public.interconnector_broadcast_targets_id_seq'::regclass);


--
-- Name: interconnector_conflicts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_conflicts ALTER COLUMN id SET DEFAULT nextval('public.interconnector_conflicts_id_seq'::regclass);


--
-- Name: interconnector_learnings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_learnings ALTER COLUMN id SET DEFAULT nextval('public.interconnector_learnings_id_seq'::regclass);


--
-- Name: interconnector_pending_approvals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_pending_approvals ALTER COLUMN id SET DEFAULT nextval('public.interconnector_pending_approvals_id_seq'::regclass);


--
-- Name: interconnector_pending_changes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_pending_changes ALTER COLUMN id SET DEFAULT nextval('public.interconnector_pending_changes_id_seq'::regclass);


--
-- Name: interconnector_sync_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_sync_history ALTER COLUMN id SET DEFAULT nextval('public.interconnector_sync_history_id_seq'::regclass);


--
-- Name: interconnector_sync_profiles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_sync_profiles ALTER COLUMN id SET DEFAULT nextval('public.interconnector_sync_profiles_id_seq'::regclass);


--
-- Name: llm_messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_messages ALTER COLUMN id SET DEFAULT nextval('public.llm_messages_id_seq'::regclass);


--
-- Name: llm_session_summaries id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_session_summaries ALTER COLUMN id SET DEFAULT nextval('public.llm_session_summaries_id_seq'::regclass);


--
-- Name: models id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.models ALTER COLUMN id SET DEFAULT nextval('public.models_id_seq'::regclass);


--
-- Name: music_videos id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.music_videos ALTER COLUMN id SET DEFAULT nextval('public.music_videos_id_seq'::regclass);


--
-- Name: pending_fixes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pending_fixes ALTER COLUMN id SET DEFAULT nextval('public.pending_fixes_id_seq'::regclass);


--
-- Name: production_shot_subjects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_shot_subjects ALTER COLUMN id SET DEFAULT nextval('public.production_shot_subjects_id_seq'::regclass);


--
-- Name: production_shots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_shots ALTER COLUMN id SET DEFAULT nextval('public.production_shots_id_seq'::regclass);


--
-- Name: production_subjects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_subjects ALTER COLUMN id SET DEFAULT nextval('public.production_subjects_id_seq'::regclass);


--
-- Name: productions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.productions ALTER COLUMN id SET DEFAULT nextval('public.productions_id_seq'::regclass);


--
-- Name: projects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects ALTER COLUMN id SET DEFAULT nextval('public.projects_id_seq'::regclass);


--
-- Name: retention_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retention_audit ALTER COLUMN id SET DEFAULT nextval('public.retention_audit_id_seq'::regclass);


--
-- Name: rules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rules ALTER COLUMN id SET DEFAULT nextval('public.rules_id_seq'::regclass);


--
-- Name: self_improvement_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.self_improvement_runs ALTER COLUMN id SET DEFAULT nextval('public.self_improvement_runs_id_seq'::regclass);


--
-- Name: social_outreach_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_outreach_log ALTER COLUMN id SET DEFAULT nextval('public.social_outreach_log_id_seq'::regclass);


--
-- Name: subjects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subjects ALTER COLUMN id SET DEFAULT nextval('public.subjects_id_seq'::regclass);


--
-- Name: swarm_messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.swarm_messages ALTER COLUMN id SET DEFAULT nextval('public.swarm_messages_id_seq'::regclass);


--
-- Name: tasks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks ALTER COLUMN id SET DEFAULT nextval('public.tasks_id_seq'::regclass);


--
-- Name: tool_feedback id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_feedback ALTER COLUMN id SET DEFAULT nextval('public.tool_feedback_id_seq'::regclass);


--
-- Name: training_datasets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_datasets ALTER COLUMN id SET DEFAULT nextval('public.training_datasets_id_seq'::regclass);


--
-- Name: training_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_jobs ALTER COLUMN id SET DEFAULT nextval('public.training_jobs_id_seq'::regclass);


--
-- Name: website_pages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_pages ALTER COLUMN id SET DEFAULT nextval('public.website_pages_id_seq'::regclass);


--
-- Name: websites id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.websites ALTER COLUMN id SET DEFAULT nextval('public.websites_id_seq'::regclass);


--
-- Name: wordpress_pages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wordpress_pages ALTER COLUMN id SET DEFAULT nextval('public.wordpress_pages_id_seq'::regclass);


--
-- Name: wordpress_sites id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wordpress_sites ALTER COLUMN id SET DEFAULT nextval('public.wordpress_sites_id_seq'::regclass);


--
-- Name: agent_action_provenance agent_action_provenance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_action_provenance
    ADD CONSTRAINT agent_action_provenance_pkey PRIMARY KEY (id);


--
-- Name: agent_memories agent_memories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_memories
    ADD CONSTRAINT agent_memories_pkey PRIMARY KEY (id);


--
-- Name: agent_memory_audit agent_memory_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_memory_audit
    ADD CONSTRAINT agent_memory_audit_pkey PRIMARY KEY (id);


--
-- Name: batch_job_columns batch_job_columns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batch_job_columns
    ADD CONSTRAINT batch_job_columns_pkey PRIMARY KEY (id);


--
-- Name: batch_job_rows batch_job_rows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batch_job_rows
    ADD CONSTRAINT batch_job_rows_pkey PRIMARY KEY (id);


--
-- Name: clients clients_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_pkey PRIMARY KEY (id);


--
-- Name: demo_steps demo_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.demo_steps
    ADD CONSTRAINT demo_steps_pkey PRIMARY KEY (id);


--
-- Name: demonstrations demonstrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.demonstrations
    ADD CONSTRAINT demonstrations_pkey PRIMARY KEY (id);


--
-- Name: device_profiles device_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_profiles
    ADD CONSTRAINT device_profiles_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: eval_pairs eval_pairs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_pairs
    ADD CONSTRAINT eval_pairs_pkey PRIMARY KEY (id);


--
-- Name: experiment_runs experiment_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_runs
    ADD CONSTRAINT experiment_runs_pkey PRIMARY KEY (id);


--
-- Name: folders folders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT folders_pkey PRIMARY KEY (id);


--
-- Name: generations generations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generations
    ADD CONSTRAINT generations_pkey PRIMARY KEY (id);


--
-- Name: google_indexing_config google_indexing_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_indexing_config
    ADD CONSTRAINT google_indexing_config_pkey PRIMARY KEY (id);


--
-- Name: google_indexing_submissions google_indexing_submissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_indexing_submissions
    ADD CONSTRAINT google_indexing_submissions_pkey PRIMARY KEY (id);


--
-- Name: images images_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.images
    ADD CONSTRAINT images_pkey PRIMARY KEY (id);


--
-- Name: interconnector_broadcast_targets interconnector_broadcast_targets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_broadcast_targets
    ADD CONSTRAINT interconnector_broadcast_targets_pkey PRIMARY KEY (id);


--
-- Name: interconnector_broadcasts interconnector_broadcasts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_broadcasts
    ADD CONSTRAINT interconnector_broadcasts_pkey PRIMARY KEY (id);


--
-- Name: interconnector_conflicts interconnector_conflicts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_conflicts
    ADD CONSTRAINT interconnector_conflicts_pkey PRIMARY KEY (id);


--
-- Name: interconnector_learnings interconnector_learnings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_learnings
    ADD CONSTRAINT interconnector_learnings_pkey PRIMARY KEY (id);


--
-- Name: interconnector_pending_approvals interconnector_pending_approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_pending_approvals
    ADD CONSTRAINT interconnector_pending_approvals_pkey PRIMARY KEY (id);


--
-- Name: interconnector_pending_changes interconnector_pending_changes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_pending_changes
    ADD CONSTRAINT interconnector_pending_changes_pkey PRIMARY KEY (id);


--
-- Name: interconnector_sync_history interconnector_sync_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_sync_history
    ADD CONSTRAINT interconnector_sync_history_pkey PRIMARY KEY (id);


--
-- Name: interconnector_sync_profiles interconnector_sync_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interconnector_sync_profiles
    ADD CONSTRAINT interconnector_sync_profiles_pkey PRIMARY KEY (id);


--
-- Name: job_history job_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_history
    ADD CONSTRAINT job_history_pkey PRIMARY KEY (id);


--
-- Name: llm_messages llm_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_messages
    ADD CONSTRAINT llm_messages_pkey PRIMARY KEY (id);


--
-- Name: llm_session_summaries llm_session_summaries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_session_summaries
    ADD CONSTRAINT llm_session_summaries_pkey PRIMARY KEY (id);


--
-- Name: llm_sessions llm_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_sessions
    ADD CONSTRAINT llm_sessions_pkey PRIMARY KEY (id);


--
-- Name: models models_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.models
    ADD CONSTRAINT models_pkey PRIMARY KEY (id);


--
-- Name: music_videos music_videos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.music_videos
    ADD CONSTRAINT music_videos_pkey PRIMARY KEY (id);


--
-- Name: pages pages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_pkey PRIMARY KEY (id);


--
-- Name: pending_fixes pending_fixes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pending_fixes
    ADD CONSTRAINT pending_fixes_pkey PRIMARY KEY (id);


--
-- Name: production_shot_subjects production_shot_subjects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_shot_subjects
    ADD CONSTRAINT production_shot_subjects_pkey PRIMARY KEY (id);


--
-- Name: production_shots production_shots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_shots
    ADD CONSTRAINT production_shots_pkey PRIMARY KEY (id);


--
-- Name: production_subjects production_subjects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_subjects
    ADD CONSTRAINT production_subjects_pkey PRIMARY KEY (id);


--
-- Name: productions productions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.productions
    ADD CONSTRAINT productions_pkey PRIMARY KEY (id);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: research_configs research_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.research_configs
    ADD CONSTRAINT research_configs_pkey PRIMARY KEY (id);


--
-- Name: retention_audit retention_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retention_audit
    ADD CONSTRAINT retention_audit_pkey PRIMARY KEY (id);


--
-- Name: rules rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rules
    ADD CONSTRAINT rules_pkey PRIMARY KEY (id);


--
-- Name: self_improvement_runs self_improvement_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.self_improvement_runs
    ADD CONSTRAINT self_improvement_runs_pkey PRIMARY KEY (id);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (key);


--
-- Name: social_outreach_log social_outreach_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_outreach_log
    ADD CONSTRAINT social_outreach_log_pkey PRIMARY KEY (id);


--
-- Name: subjects subjects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_pkey PRIMARY KEY (id);


--
-- Name: swarm_messages swarm_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.swarm_messages
    ADD CONSTRAINT swarm_messages_pkey PRIMARY KEY (id);


--
-- Name: symbol_hits symbol_hits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.symbol_hits
    ADD CONSTRAINT symbol_hits_pkey PRIMARY KEY (symbol_id);


--
-- Name: system_settings system_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_settings
    ADD CONSTRAINT system_settings_pkey PRIMARY KEY (key);


--
-- Name: tasks tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);


--
-- Name: tool_feedback tool_feedback_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_feedback
    ADD CONSTRAINT tool_feedback_pkey PRIMARY KEY (id);


--
-- Name: training_datasets training_datasets_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_datasets
    ADD CONSTRAINT training_datasets_name_key UNIQUE (name);


--
-- Name: training_datasets training_datasets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_datasets
    ADD CONSTRAINT training_datasets_pkey PRIMARY KEY (id);


--
-- Name: training_jobs training_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_jobs
    ADD CONSTRAINT training_jobs_pkey PRIMARY KEY (id);


--
-- Name: demo_steps uq_demo_step_index; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.demo_steps
    ADD CONSTRAINT uq_demo_step_index UNIQUE (demonstration_id, step_index);


--
-- Name: google_indexing_submissions uq_gindex_website_url; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_indexing_submissions
    ADD CONSTRAINT uq_gindex_website_url UNIQUE (website_id, url);


--
-- Name: production_subjects uq_production_subject; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_subjects
    ADD CONSTRAINT uq_production_subject UNIQUE (production_id, subject_id);


--
-- Name: website_pages uq_website_page_url; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_pages
    ADD CONSTRAINT uq_website_page_url UNIQUE (website_id, url);


--
-- Name: website_pages website_pages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_pages
    ADD CONSTRAINT website_pages_pkey PRIMARY KEY (id);


--
-- Name: websites websites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.websites
    ADD CONSTRAINT websites_pkey PRIMARY KEY (id);


--
-- Name: websites websites_url_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.websites
    ADD CONSTRAINT websites_url_key UNIQUE (url);


--
-- Name: wordpress_pages wordpress_pages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wordpress_pages
    ADD CONSTRAINT wordpress_pages_pkey PRIMARY KEY (id);


--
-- Name: wordpress_sites wordpress_sites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wordpress_sites
    ADD CONSTRAINT wordpress_sites_pkey PRIMARY KEY (id);


--
-- Name: wordpress_sites wordpress_sites_url_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wordpress_sites
    ADD CONSTRAINT wordpress_sites_url_key UNIQUE (url);


--
-- Name: idx_batch_job_rows_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batch_job_rows_job_id ON public.batch_job_rows USING btree (job_id);


--
-- Name: ix_agent_action_provenance_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_action_provenance_created_at ON public.agent_action_provenance USING btree (created_at);


--
-- Name: ix_agent_action_provenance_request_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_action_provenance_request_id ON public.agent_action_provenance USING btree (request_id);


--
-- Name: ix_agent_action_provenance_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_action_provenance_session_id ON public.agent_action_provenance USING btree (session_id);


--
-- Name: ix_agent_memories_importance; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_memories_importance ON public.agent_memories USING btree (importance);


--
-- Name: ix_agent_memories_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_memories_session_id ON public.agent_memories USING btree (session_id);


--
-- Name: ix_agent_memories_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_memories_source ON public.agent_memories USING btree (source);


--
-- Name: ix_agent_memories_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_memories_type ON public.agent_memories USING btree (type);


--
-- Name: ix_agent_memory_audit_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_memory_audit_action ON public.agent_memory_audit USING btree (action);


--
-- Name: ix_agent_memory_audit_actor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_memory_audit_actor ON public.agent_memory_audit USING btree (actor);


--
-- Name: ix_agent_memory_audit_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_memory_audit_created_at ON public.agent_memory_audit USING btree (created_at);


--
-- Name: ix_agent_memory_audit_memory_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_memory_audit_memory_id ON public.agent_memory_audit USING btree (memory_id);


--
-- Name: ix_demo_steps_demonstration_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_demo_steps_demonstration_id ON public.demo_steps USING btree (demonstration_id);


--
-- Name: ix_demonstrations_autonomy_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_demonstrations_autonomy_level ON public.demonstrations USING btree (autonomy_level);


--
-- Name: ix_demonstrations_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_demonstrations_name ON public.demonstrations USING btree (name);


--
-- Name: ix_demonstrations_parent_demonstration_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_demonstrations_parent_demonstration_id ON public.demonstrations USING btree (parent_demonstration_id);


--
-- Name: ix_google_indexing_config_website_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_google_indexing_config_website_id ON public.google_indexing_config USING btree (website_id);


--
-- Name: ix_google_indexing_submissions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_google_indexing_submissions_status ON public.google_indexing_submissions USING btree (status);


--
-- Name: ix_google_indexing_submissions_url; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_google_indexing_submissions_url ON public.google_indexing_submissions USING btree (url);


--
-- Name: ix_google_indexing_submissions_website_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_google_indexing_submissions_website_id ON public.google_indexing_submissions USING btree (website_id);


--
-- Name: ix_job_history_finished; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_history_finished ON public.job_history USING btree (finished_at DESC);


--
-- Name: ix_job_history_kind_finished; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_history_kind_finished ON public.job_history USING btree (kind, finished_at DESC);


--
-- Name: ix_job_history_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_history_status ON public.job_history USING btree (status);


--
-- Name: ix_llm_session_summaries_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_session_summaries_created_at ON public.llm_session_summaries USING btree (created_at);


--
-- Name: ix_llm_session_summaries_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_session_summaries_session_id ON public.llm_session_summaries USING btree (session_id);


--
-- Name: ix_music_videos_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_music_videos_project_id ON public.music_videos USING btree (project_id);


--
-- Name: ix_music_videos_song_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_music_videos_song_document_id ON public.music_videos USING btree (song_document_id);


--
-- Name: ix_music_videos_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_music_videos_status ON public.music_videos USING btree (status);


--
-- Name: ix_production_shot_subjects_shot_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_shot_subjects_shot_id ON public.production_shot_subjects USING btree (shot_id);


--
-- Name: ix_production_shot_subjects_subject_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_shot_subjects_subject_id ON public.production_shot_subjects USING btree (subject_id);


--
-- Name: ix_production_shots_production_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_shots_production_id ON public.production_shots USING btree (production_id);


--
-- Name: ix_production_subjects_production_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_subjects_production_id ON public.production_subjects USING btree (production_id);


--
-- Name: ix_production_subjects_subject_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_subjects_subject_id ON public.production_subjects USING btree (subject_id);


--
-- Name: ix_productions_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_productions_project_id ON public.productions USING btree (project_id);


--
-- Name: ix_productions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_productions_status ON public.productions USING btree (status);


--
-- Name: ix_retention_audit_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_retention_audit_kind ON public.retention_audit USING btree (kind);


--
-- Name: ix_retention_audit_occurred; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_retention_audit_occurred ON public.retention_audit USING btree (occurred_at DESC);


--
-- Name: ix_social_outreach_log_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_social_outreach_log_created_at ON public.social_outreach_log USING btree (created_at);


--
-- Name: ix_social_outreach_log_platform; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_social_outreach_log_platform ON public.social_outreach_log USING btree (platform);


--
-- Name: ix_social_outreach_log_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_social_outreach_log_status ON public.social_outreach_log USING btree (status);


--
-- Name: ix_social_outreach_log_target_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_social_outreach_log_target_thread_id ON public.social_outreach_log USING btree (target_thread_id);


--
-- Name: ix_social_outreach_log_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_social_outreach_log_task_id ON public.social_outreach_log USING btree (task_id);


--
-- Name: ix_subjects_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_subjects_kind ON public.subjects USING btree (kind);


--
-- Name: ix_subjects_training_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_subjects_training_status ON public.subjects USING btree (training_status);


--
-- Name: ix_swarm_messages_agent_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_swarm_messages_agent_name ON public.swarm_messages USING btree (agent_name);


--
-- Name: ix_swarm_messages_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_swarm_messages_created_at ON public.swarm_messages USING btree (created_at);


--
-- Name: ix_swarm_messages_production_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_swarm_messages_production_id ON public.swarm_messages USING btree (production_id);


--
-- Name: ix_symbol_hits_last_fired_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_symbol_hits_last_fired_at ON public.symbol_hits USING btree (last_fired_at);


--
-- Name: ix_tasks_client_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_client_id ON public.tasks USING btree (client_id);


--
-- Name: ix_tasks_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_created_at ON public.tasks USING btree (created_at);


--
-- Name: ix_tasks_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_job_id ON public.tasks USING btree (job_id);


--
-- Name: ix_tasks_next_run_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_next_run_at ON public.tasks USING btree (next_run_at);


--
-- Name: ix_tasks_parent_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_parent_task_id ON public.tasks USING btree (parent_task_id);


--
-- Name: ix_tasks_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_project_id ON public.tasks USING btree (project_id);


--
-- Name: ix_tasks_schedule_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_schedule_type ON public.tasks USING btree (schedule_type);


--
-- Name: ix_tasks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_status ON public.tasks USING btree (status);


--
-- Name: ix_tasks_task_handler; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_task_handler ON public.tasks USING btree (task_handler);


--
-- Name: ix_tasks_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_type ON public.tasks USING btree (type);


--
-- Name: ix_tasks_website_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_website_id ON public.tasks USING btree (website_id);


--
-- Name: ix_tool_feedback_lesson_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tool_feedback_lesson_id ON public.tool_feedback USING btree (lesson_id);


--
-- Name: ix_tool_feedback_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tool_feedback_session_id ON public.tool_feedback USING btree (session_id);


--
-- Name: ix_tool_feedback_tool_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tool_feedback_tool_name ON public.tool_feedback USING btree (tool_name);


--
-- Name: ix_training_jobs_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_training_jobs_job_id ON public.training_jobs USING btree (job_id);


--
-- Name: ix_website_pages_crawled_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_website_pages_crawled_at ON public.website_pages USING btree (crawled_at);


--
-- Name: ix_website_pages_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_website_pages_slug ON public.website_pages USING btree (slug);


--
-- Name: ix_website_pages_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_website_pages_status ON public.website_pages USING btree (status);


--
-- Name: ix_website_pages_url; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_website_pages_url ON public.website_pages USING btree (url);


--
-- Name: ix_website_pages_website_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_website_pages_website_id ON public.website_pages USING btree (website_id);


--
-- Name: ix_websites_client_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_websites_client_id ON public.websites USING btree (client_id);


--
-- Name: ix_websites_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_websites_project_id ON public.websites USING btree (project_id);


--
-- Name: ix_websites_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_websites_status ON public.websites USING btree (status);


--
-- Name: ix_wordpress_pages_post_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_pages_post_type ON public.wordpress_pages USING btree (post_type);


--
-- Name: ix_wordpress_pages_process_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_pages_process_status ON public.wordpress_pages USING btree (process_status);


--
-- Name: ix_wordpress_pages_pull_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_pages_pull_status ON public.wordpress_pages USING btree (pull_status);


--
-- Name: ix_wordpress_pages_push_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_pages_push_status ON public.wordpress_pages USING btree (push_status);


--
-- Name: ix_wordpress_pages_review_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_pages_review_status ON public.wordpress_pages USING btree (review_status);


--
-- Name: ix_wordpress_pages_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_pages_slug ON public.wordpress_pages USING btree (slug);


--
-- Name: ix_wordpress_pages_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_pages_status ON public.wordpress_pages USING btree (status);


--
-- Name: ix_wordpress_pages_wordpress_post_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_pages_wordpress_post_id ON public.wordpress_pages USING btree (wordpress_post_id);


--
-- Name: ix_wordpress_pages_wordpress_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_pages_wordpress_site_id ON public.wordpress_pages USING btree (wordpress_site_id);


--
-- Name: ix_wordpress_sites_client_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_sites_client_id ON public.wordpress_sites USING btree (client_id);


--
-- Name: ix_wordpress_sites_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_sites_project_id ON public.wordpress_sites USING btree (project_id);


--
-- Name: ix_wordpress_sites_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_sites_status ON public.wordpress_sites USING btree (status);


--
-- Name: ix_wordpress_sites_website_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wordpress_sites_website_id ON public.wordpress_sites USING btree (website_id);


--
-- Name: ix_wp_page_analytics_synced; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wp_page_analytics_synced ON public.wordpress_pages USING btree (analytics_synced_at);


--
-- Name: ix_wp_page_process_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wp_page_process_status ON public.wordpress_pages USING btree (process_status, pull_status);


--
-- Name: ix_wp_page_review_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wp_page_review_status ON public.wordpress_pages USING btree (review_status, process_status);


--
-- Name: ix_wp_page_seo_plugin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wp_page_seo_plugin ON public.wordpress_pages USING btree (seo_plugin);


--
-- Name: ix_wp_page_seo_score; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wp_page_seo_score ON public.wordpress_pages USING btree (seo_score);


--
-- Name: ix_wp_page_site_post; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wp_page_site_post ON public.wordpress_pages USING btree (wordpress_site_id, wordpress_post_id);


--
-- Name: uq_doc_folder_filename; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_doc_folder_filename ON public.documents USING btree (folder_id, filename) NULLS NOT DISTINCT;


--
-- Name: demonstrations fk_demo_parent_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.demonstrations
    ADD CONSTRAINT fk_demo_parent_id FOREIGN KEY (parent_demonstration_id) REFERENCES public.demonstrations(id) ON DELETE SET NULL;


--
-- Name: demo_steps fk_demostep_demo_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.demo_steps
    ADD CONSTRAINT fk_demostep_demo_id FOREIGN KEY (demonstration_id) REFERENCES public.demonstrations(id) ON DELETE CASCADE;


--
-- Name: google_indexing_config fk_gindex_cfg_website_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_indexing_config
    ADD CONSTRAINT fk_gindex_cfg_website_id FOREIGN KEY (website_id) REFERENCES public.websites(id) ON DELETE CASCADE;


--
-- Name: google_indexing_submissions fk_gindex_sub_website_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_indexing_submissions
    ADD CONSTRAINT fk_gindex_sub_website_id FOREIGN KEY (website_id) REFERENCES public.websites(id) ON DELETE CASCADE;


--
-- Name: llm_session_summaries fk_llmsummary_session_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_session_summaries
    ADD CONSTRAINT fk_llmsummary_session_id FOREIGN KEY (session_id) REFERENCES public.llm_sessions(id) ON DELETE CASCADE;


--
-- Name: music_videos fk_music_video_output_document_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.music_videos
    ADD CONSTRAINT fk_music_video_output_document_id FOREIGN KEY (output_document_id) REFERENCES public.documents(id) ON DELETE SET NULL;


--
-- Name: music_videos fk_music_video_project_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.music_videos
    ADD CONSTRAINT fk_music_video_project_id FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;


--
-- Name: music_videos fk_music_video_song_document_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.music_videos
    ADD CONSTRAINT fk_music_video_song_document_id FOREIGN KEY (song_document_id) REFERENCES public.documents(id) ON DELETE SET NULL;


--
-- Name: productions fk_production_project_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.productions
    ADD CONSTRAINT fk_production_project_id FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;


--
-- Name: production_shots fk_production_shot_production_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_shots
    ADD CONSTRAINT fk_production_shot_production_id FOREIGN KEY (production_id) REFERENCES public.productions(id) ON DELETE CASCADE;


--
-- Name: production_shots fk_production_shot_voice_subject_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_shots
    ADD CONSTRAINT fk_production_shot_voice_subject_id FOREIGN KEY (voice_subject_id) REFERENCES public.subjects(id) ON DELETE SET NULL;


--
-- Name: production_subjects fk_production_subject_production_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_subjects
    ADD CONSTRAINT fk_production_subject_production_id FOREIGN KEY (production_id) REFERENCES public.productions(id) ON DELETE CASCADE;


--
-- Name: production_subjects fk_production_subject_subject_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_subjects
    ADD CONSTRAINT fk_production_subject_subject_id FOREIGN KEY (subject_id) REFERENCES public.subjects(id) ON DELETE CASCADE;


--
-- Name: production_shot_subjects fk_pss_shot_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_shot_subjects
    ADD CONSTRAINT fk_pss_shot_id FOREIGN KEY (shot_id) REFERENCES public.production_shots(id) ON DELETE CASCADE;


--
-- Name: production_shot_subjects fk_pss_subject_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_shot_subjects
    ADD CONSTRAINT fk_pss_subject_id FOREIGN KEY (subject_id) REFERENCES public.subjects(id) ON DELETE CASCADE;


--
-- Name: social_outreach_log fk_social_outreach_task_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_outreach_log
    ADD CONSTRAINT fk_social_outreach_task_id FOREIGN KEY (task_id) REFERENCES public.tasks(id) ON DELETE SET NULL;


--
-- Name: swarm_messages fk_swarm_message_production_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.swarm_messages
    ADD CONSTRAINT fk_swarm_message_production_id FOREIGN KEY (production_id) REFERENCES public.productions(id) ON DELETE CASCADE;


--
-- Name: tasks fk_task_client_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT fk_task_client_id FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE SET NULL;


--
-- Name: tasks fk_task_parent_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT fk_task_parent_id FOREIGN KEY (parent_task_id) REFERENCES public.tasks(id) ON DELETE SET NULL;


--
-- Name: tasks fk_task_project_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT fk_task_project_id FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;


--
-- Name: tasks fk_task_website_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT fk_task_website_id FOREIGN KEY (website_id) REFERENCES public.websites(id) ON DELETE SET NULL;


--
-- Name: websites fk_website_client_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.websites
    ADD CONSTRAINT fk_website_client_id FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE SET NULL;


--
-- Name: website_pages fk_website_page_site_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_pages
    ADD CONSTRAINT fk_website_page_site_id FOREIGN KEY (website_id) REFERENCES public.websites(id) ON DELETE CASCADE;


--
-- Name: websites fk_website_project_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.websites
    ADD CONSTRAINT fk_website_project_id FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;


--
-- Name: wordpress_pages fk_wp_page_site_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wordpress_pages
    ADD CONSTRAINT fk_wp_page_site_id FOREIGN KEY (wordpress_site_id) REFERENCES public.wordpress_sites(id) ON DELETE CASCADE;


--
-- Name: wordpress_sites fk_wp_site_client_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wordpress_sites
    ADD CONSTRAINT fk_wp_site_client_id FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE SET NULL;


--
-- Name: wordpress_sites fk_wp_site_project_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wordpress_sites
    ADD CONSTRAINT fk_wp_site_project_id FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;


--
-- Name: wordpress_sites fk_wp_site_website_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wordpress_sites
    ADD CONSTRAINT fk_wp_site_website_id FOREIGN KEY (website_id) REFERENCES public.websites(id) ON DELETE SET NULL;


--
-- Name: training_jobs training_jobs_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_jobs
    ADD CONSTRAINT training_jobs_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.training_datasets(id);


--
-- Name: training_jobs training_jobs_device_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_jobs
    ADD CONSTRAINT training_jobs_device_profile_id_fkey FOREIGN KEY (device_profile_id) REFERENCES public.device_profiles(id);


--
-- PostgreSQL database dump complete
--
