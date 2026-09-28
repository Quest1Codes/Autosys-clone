"""
Static catalog of JIL attribute names, used by the lexer.

* JOB_ATTRS   -- the 447 job attributes documented in the vendor PDF
  (one <name> Attribute -- ... reference section each).
* OTHER_ATTRS -- attribute names used by the non-job stanzas
  (machines, resources, xinst, blobs, globs, monitors, views, filters, ...),
  harvested from the PDF's own examples.
* CANONICAL_CASE -- the PDF spells a handful of attributes in mixed case
  (URL, WSDL_URL, endpoint_URL, ftp_use_SSL).  JIL keywords are
  otherwise lower-case, so the lexer lower-cases every key EXCEPT those that
  appear here, which are mapped back to their documented spelling.
"""

from __future__ import annotations

JOB_ATTRS: frozenset[str] = frozenset({
    'after_time', 'agent_name', 'alarm', 'alarm_if_fail', 'alarm_if_terminated', 'alarm_verif',
    'all_events', 'all_status', 'amount', 'application', 'arc_obj_name', 'arc_obj_variant',
    'arc_parms', 'auth_string', 'auto_delete', 'auto_hold', 'avg_runtime', 'bdc_err_rate',
    'bdc_ext_log', 'bdc_proc_rate', 'bdc_system', 'bean_name', 'blob_file', 'blob_input',
    'blob_type', 'box_failure', 'box_name', 'box_success', 'broadcast_address',
    'character_code', 'chk_files', 'class_name', 'command', 'compatibility', 'condition',
    'condition_code', 'connect_string', 'connection_factory', 'connection_retry',
    'connection_timeout', 'continuous', 'copy_jcl', 'correlation_id', 'cpu_usage',
    'create_method', 'create_name', 'create_parameter', 'currun', 'date_conditions',
    'days_of_week', 'dbtype', 'description', 'destination_file', 'destination_name',
    'destination_user', 'disk_drive', 'disk_format', 'disk_space', 'elevated', 'encoding',
    'encryption_type', 'endpoint_URL', 'envvars', 'exclude_calendar', 'external_os_user',
    'factor', 'fail_codes', 'failure', 'filter', 'filter_type', 'finder_name',
    'finder_parameter', 'force', 'form_parameter', 'ftp_compression', 'ftp_local_name',
    'ftp_local_user', 'ftp_remote_name', 'ftp_server_name', 'ftp_server_port',
    'ftp_transfer_direction', 'ftp_transfer_type', 'ftp_use_SSL', 'ftp_user_type', 'group',
    'header_filter', 'headers', 'heartbeat_attempts', 'heartbeat_freq', 'heartbeat_interval',
    'i5_action', 'i5_cc_exit', 'i5_curr_lib', 'i5_job_desc', 'i5_job_name', 'i5_job_queue',
    'i5_lda', 'i5_lib', 'i5_library_list', 'i5_name', 'i5_others', 'i5_params',
    'i5_process_priority', 'informatica_folder_name', 'informatica_param_file',
    'informatica_pass_on_success', 'informatica_repository_name',
    'informatica_security_domain', 'informatica_target_name', 'informatica_task_name',
    'informatica_user', 'informatica_workflow_inst_name', 'informatica_workflow_name',
    'initial_context_factory', 'input_cookies', 'inside_range', 'interactive',
    'invocation_type', 'ip_host', 'ip_port', 'ip_status', 'j2ee_authentication_order',
    'j2ee_conn_domain', 'j2ee_conn_origin', 'j2ee_conn_user', 'j2ee_no_global_proxy_defaults',
    'j2ee_parameter', 'j2ee_proxy_domain', 'j2ee_proxy_host', 'j2ee_proxy_origin_host',
    'j2ee_proxy_port', 'j2ee_proxy_user', 'j2ee_user', 'jcl_library', 'jcl_member',
    'jmx_parameter', 'jmx_user', 'job_class', 'job_criteria', 'job_filter', 'job_load',
    'job_name', 'job_terminator', 'job_type', 'key_to_agent', 'lower_boundary', 'mac_address',
    'machine', 'machine_method', 'max_exit_success', 'max_load', 'max_run_alarm', 'mbean_attr',
    'mbean_name', 'mbean_operation', 'media_type', 'message_class', 'method_name',
    'mf_jcl_name', 'mf_jcl_type', 'mf_server', 'mf_server_address_type', 'mf_user',
    'mf_version', 'min_run_alarm', 'mode', 'modify_parameter', 'monitor_cond', 'monitor_mode',
    'monitor_type', 'must_complete_times', 'must_start_times', 'n_retrys', 'no_change',
    'node_name', 'notification_alarm_types', 'notification_emailaddress',
    'notification_emailaddress_on_alarm', 'notification_emailaddress_on_failure',
    'notification_emailaddress_on_success', 'notification_emailaddress_on_terminated',
    'notification_msg', 'one_way', 'operation_type', 'opsys', 'oracle_appl_name',
    'oracle_appl_name_type', 'oracle_args', 'oracle_custom_property', 'oracle_desc',
    'oracle_mon_children', 'oracle_mon_children_delay', 'oracle_notify_display_users',
    'oracle_notify_users', 'oracle_oprunit', 'oracle_output_format', 'oracle_print_copies',
    'oracle_print_style', 'oracle_printer', 'oracle_program', 'oracle_program_name_type',
    'oracle_programdata', 'oracle_quote_in_default', 'oracle_req_set', 'oracle_req_set_type',
    'oracle_resp', 'oracle_save_output', 'oracle_template_language', 'oracle_template_name',
    'oracle_template_territory', 'oracle_use_arg_def', 'oracle_use_set_defaults_first',
    'oracle_user', 'os_user', 'output_var', 'owner', 'pa_monitor_progress', 'pa_name',
    'pa_parameter', 'pa_path', 'pa_trace', 'payload_uri', 'permission',
    'persist_output_cookies', 'persist_output_header', 'persist_output_payload', 'ping_ports',
    'ping_timeout', 'poll_interval', 'port', 'port_name', 'preemptive_authentication',
    'priority', 'process_name', 'process_status', 'profile', 'provider_url', 'ps_args',
    'ps_dest_format', 'ps_dest_type', 'ps_detail_folder', 'ps_dlist_roles', 'ps_dlist_users',
    'ps_email_address', 'ps_email_address_expanded', 'ps_email_log', 'ps_email_subject',
    'ps_email_text', 'ps_email_web_report', 'ps_operator_id', 'ps_output_dest',
    'ps_process_name', 'ps_process_type', 'ps_restarts', 'ps_run_cntrl_args',
    'ps_run_cntrl_id', 'ps_run_control_table', 'ps_server_name', 'ps_skip_parm_updates',
    'ps_time_zone', 'remote_command', 'remote_name', 'remote_target', 'remove_references',
    'reply_to', 'request_id', 'res_type', 'resources', 'restart', 'result_type',
    'results_file', 'results_file_overwrite', 'retry_on_failure_count',
    'retry_on_failure_delay', 'retry_on_failure_details', 'return_class_name',
    'return_namespace', 'return_xml_name', 'run_calendar', 'run_external', 'run_window',
    'running', 'sap_abap_name', 'sap_chain_id', 'sap_client', 'sap_ext_table', 'sap_fail_msg',
    'sap_info_pack', 'sap_job_class', 'sap_job_count', 'sap_job_name', 'sap_lang',
    'sap_mon_child', 'sap_office', 'sap_print_parms', 'sap_proc_type', 'sap_process_client',
    'sap_process_status', 'sap_recipients', 'sap_release_option', 'sap_rfc_dest',
    'sap_step_num', 'sap_step_parms', 'sap_success_msg', 'sap_target_jobname',
    'sap_target_sys', 'scp_create_targetdir', 'scp_delete_sourcedir', 'scp_delete_sourcefile',
    'scp_local_name', 'scp_local_user', 'scp_protocol', 'scp_remote_dir', 'scp_remote_name',
    'scp_rename_sourcefile', 'scp_server_name', 'scp_server_port', 'scp_target_os',
    'scp_transfer_direction', 'scp_transfer_mode', 'search_bw', 'send_notification',
    'service_desk', 'service_name', 'shell', 'snmp_auth_protocol', 'snmp_comm_string',
    'snmp_context_engine_id', 'snmp_context_name', 'snmp_host', 'snmp_mib', 'snmp_oid',
    'snmp_privacy', 'snmp_privacy_user', 'snmp_subtree', 'snmp_table_view', 'snmp_value',
    'snmp_version', 'soap_action', 'soap_version', 'sp_arg', 'sp_name', 'spool_file',
    'sql_command', 'sqlagent_domain_name', 'sqlagent_jobid', 'sqlagent_jobname',
    'sqlagent_server_name', 'sqlagent_step_name', 'sqlagent_target_db', 'sqlagent_user_name',
    'start_mins', 'start_times', 'starting', 'std_err_file', 'std_in_file', 'std_out_file',
    'submit_modifier', 'success', 'success_codes', 'success_criteria', 'success_criteria_json',
    'success_criteria_regex', 'success_criteria_value', 'success_criteria_xpath',
    'success_pattern', 'suspended', 'svcdesk_attr', 'svcdesk_desc', 'svcdesk_imp',
    'svcdesk_pri', 'svcdesk_sev', 'tablename', 'target_namespace', 'term_run_time',
    'terminated', 'text_file_filter', 'text_file_filter_exists', 'text_file_mode',
    'text_file_name', 'time_format', 'time_position', 'timezone', 'trigger_cond',
    'trigger_type', 'type', 'ulimit', 'upper_boundary', 'URL', 'use_topic', 'user_role',
    'wake_password', 'watch_file', 'watch_file_change_type', 'watch_file_change_value',
    'watch_file_groupname', 'watch_file_min_size', 'watch_file_owner', 'watch_file_recursive',
    'watch_file_type', 'watch_file_win_user', 'watch_interval', 'watch_no_change',
    'web_parameter', 'web_user', 'win_event_category', 'win_event_computer',
    'win_event_datetime', 'win_event_description', 'win_event_id', 'win_event_op',
    'win_event_source', 'win_event_type', 'win_log_name', 'win_service_name',
    'win_service_status', 'ws_authentication_order', 'ws_conn_domain', 'ws_conn_origin',
    'ws_conn_user', 'ws_global_proxy_defaults', 'ws_parameter', 'ws_proxy_domain',
    'ws_proxy_host', 'ws_proxy_origin', 'ws_proxy_port', 'ws_proxy_user', 'ws_security',
    'wsdl_operation', 'WSDL_URL', 'xcrypt_type', 'xkey_to_manager', 'xmachine', 'xmanager',
    'xport', 'xtype', 'zos_dataset', 'zos_dsn_renamed', 'zos_dsn_updated', 'zos_explicit_dsn',
    'zos_ftp_direction', 'zos_ftp_host', 'zos_ftp_userid', 'zos_jobname', 'zos_trigger_by',
    'zos_trigger_on', 'zos_trigger_type',
})

OTHER_ATTRS: frozenset[str] = frozenset({
    'active', 'alarm', 'alarm_type', 'alarm_verif', 'alert_columns', 'alert_policy',
    'all_events', 'all_status', 'amount', 'blob_file', 'blob_input', 'blob_mode', 'blob_name',
    'blob_type', 'calendar_name', 'character_code', 'connectionprofile_type', 'currun',
    'dates', 'description', 'encryption_type', 'end_date', 'factor', 'failure', 'filter',
    'force', 'global_name', 'heartbeat_interval', 'job_columns', 'job_filter', 'job_name',
    'job_status', 'job_type_name', 'key_to_agent', 'machine_method', 'machine_name',
    'max_load', 'mode', 'monbro_name', 'monbro_type', 'new_name', 'node_name', 'opsys', 'port',
    'profile_name', 'remove_references', 'render_collections', 'render_flow', 'render_jobs',
    'res_type', 'resource_name', 'restart', 'running', 'server', 'severity', 'sound',
    'start_date', 'starting', 'status', 'success', 'suspended', 'system_log', 'terminated',
    'text', 'type', 'url_link', 'url_name', 'view', 'watch_no_change', 'xcomm_alias',
    'xcrypt_type', 'xinst_name', 'xkey_to_manager', 'xmachine', 'xmanager', 'xport', 'xtype',
})

KNOWN_ATTRS_LOWER: frozenset[str] = frozenset(
    a.lower() for a in (JOB_ATTRS | OTHER_ATTRS)
)

CANONICAL_CASE: dict[str, str] = {
    a.lower(): a for a in (JOB_ATTRS | OTHER_ATTRS) if a != a.lower()
}


def canonical_key(key: str) -> str:
    """Return the documented spelling of *key* (lower-case unless the PDF uses mixed case)."""
    low = key.lower()
    return CANONICAL_CASE.get(low, low)


def is_known_attr(key: str) -> bool:
    return key.lower() in KNOWN_ATTRS_LOWER
