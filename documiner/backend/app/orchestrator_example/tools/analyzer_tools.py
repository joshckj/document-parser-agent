"""
Analyzer tools for executing SQL and data profiling.

These tools are bound to the analyzer agent and executed on the backend
(or prepared for frontend execution via CopilotKit).

Available tools:
    - execute_sql: Execute arbitrary SQL queries with validation and table access control.
    - get_table_stats: Get statistics (min, max, avg, stddev) for numeric columns.
    - check_data_quality: Check null counts and unique value counts per column.
    - delete_tables: Delete specified tables from the database.
    - get_time_coverage: Get start and end timestamps for datetime columns.
"""

from typing import Annotated, List, Optional, Any, Dict, Set
import json
import logging
from langchain_core.tools import InjectedToolArg, tool
from langchain.tools import ToolRuntime
from psycopg import AsyncConnection, sql
import sqlglot
from sqlglot import exp

from app.utils.dedup import dedup_tool_call
from app.utils.sqlglot_optimizer import postprocess_sql
from app.utils.data_utils import strip_geom_columns
from app.core.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()


def _extract_table_names(sql_query: str) -> Set[str]:
    """Extract all table names referenced in a SQL query using sqlglot.
    
    Returns set of table names (schema.table or just table).
    """
    try:
        expr = sqlglot.parse_one(sql_query, read="postgres")
        tables = set()
        for table in expr.find_all(exp.Table):
            # Build canonical name: schema.table or just table
            if table.db:
                tables.add(f"{table.db}.{table.name}")
            else:
                tables.add(table.name)
        return tables
    except Exception:
        return set()



def _get_table_identifier(table_name: str) -> Any:
    """Create a SQL identifier for a table name that might be schema-qualified.
    
    Handles 'schema.table' by creating Identifier("schema", "table").
    Handles 'table' by creating Identifier("table").
    """
    if "." in table_name:
        schema, table = table_name.split(".", 1)
        return sql.Identifier(schema, table)
    return sql.Identifier(table_name)


def get_analyzer_tools(connection: AsyncConnection, allowed_tables: Optional[List[str]] = None) -> List[Any]:
    """Get the list of tools bound to the given connection.
    
    Args:
        connection: Database connection for executing queries.
        allowed_tables: Optional list of table names the agent is allowed to query.
                       If provided, queries referencing other tables will be rejected.
    
    Creates tool functions as closures that capture the connection and allowed_tables.
    """
    
    @tool
    @dedup_tool_call
    async def execute_sql(
        sql_query: str,
        runtime: Annotated[ToolRuntime, InjectedToolArg] = None,
    ) -> str:
        """Run Postgres SQL queries for analysis.

        Args:
            sql_query: SQL query to execute;

        Returns:
            Query results as JSON string
        """
        try:
            # Table access validation
            if allowed_tables:
                referenced_tables = _extract_table_names(sql_query)
                allowed_set = set(allowed_tables)
                
                # Also allow short names (without schema) for the allowed tables
                allowed_short_names = {t.split('.')[-1] for t in allowed_tables}
                allowed_set.update(allowed_short_names)
                
                unauthorized = referenced_tables - allowed_set
                if unauthorized:
                    speedy_bad = {t for t in unauthorized if "speedy_temp" in t}
                    if speedy_bad and speedy_bad == unauthorized:
                        return (
                            "The temp table(s) " + ", ".join(sorted(speedy_bad)) +
                            " are no longer available. "
                            "Ask the orchestrator to run text_to_sql again to recreate the data."
                        )
                    return f"Access Denied: You can only query the temp table. Unauthorized tables: {', '.join(sorted(unauthorized))}"
            
            # Validation Step
            validation_result = postprocess_sql(
                sql_query,
                schema=None,
                required_columns=None, # Explicitly disabled as requested
                default_limit=_settings.default_row_limit,
                max_limit=_settings.max_row_limit,
                fail_on_schema_errors=False,
            )
            
            if not validation_result.valid:
                # Construct error message
                error_msg = f"SQL Validation Failed: {validation_result.validation.error or 'Unknown error'}"
                if validation_result.validation.error_detail:
                     error_msg += f"\nDetails: {validation_result.validation.error_detail}"
                if validation_result.lint_warnings:
                    error_msg += f"\nWarnings: {'; '.join(validation_result.lint_warnings)}"
                if validation_result.schema_validation and not validation_result.schema_validation.ok:
                    error_msg += f"\nSchema Errors: {'; '.join(validation_result.schema_validation.messages)}"
                return error_msg

            # Use the validated, strictly formatted SQL
            final_sql = validation_result.final_sql

            
            async with connection.cursor() as cur:
                await cur.execute(final_sql)
                
                if cur.description:
                    columns = [desc.name for desc in cur.description]
                    rows = await cur.fetchall()
                    
                    # Convert to list of dicts (strip geometry columns)
                    result = []
                    for row in rows:
                        item = {}
                        for i, col in enumerate(columns):
                            item[col] = row[i]
                        result.append(strip_geom_columns(item))
                    
                    return json.dumps(result, default=str, indent=2)
                else:
                    return "Query executed successfully (no rows returned)."
        except Exception as e:
            return f"Error executing SQL: {str(e)}"

    @tool
    @dedup_tool_call
    async def get_table_stats(
        tableName: str,
        column: Optional[str] = None,
        runtime: Annotated[ToolRuntime, InjectedToolArg] = None,
    ) -> str:
        """Get statistics for numeric columns in a table.

        Args:
            tableName: Name of the table to analyze
            column: Optional specific column name. If not provided, returns stats for all numeric columns.

        Returns:
            Statistics (min, max, avg, stddev) as JSON string
        """
        try:
            async with connection.cursor() as cur:
                cols_to_analyze = []
                table_id = _get_table_identifier(tableName)
                
                if column:
                    cols_to_analyze = [column]
                else:
                    # Get all columns
                    try:
                        await cur.execute(sql.SQL("SELECT * FROM {} LIMIT 0").format(table_id))
                        if cur.description:
                            cols_to_analyze = [desc.name for desc in cur.description]
                        else:
                            return f"Table {tableName} not found or empty."
                    except Exception as e:
                        return f"Error accessing table {tableName}: {e}"

                stats = {}
                for col in cols_to_analyze:
                    # Try to compute stats. If it fails (non-numeric), skip.
                    try:
                        q = sql.SQL("SELECT min({}), max({}), avg({}), stddev({}) FROM {}").format(
                            sql.Identifier(col), sql.Identifier(col), sql.Identifier(col), sql.Identifier(col),
                            table_id
                        )
                        await cur.execute(q)
                        res = await cur.fetchone()
                        if res:
                            col_stats = {
                                "min": res[0],
                                "max": res[1]
                            }
                            if res[2] is not None: col_stats["avg"] = res[2]
                            if res[3] is not None: col_stats["stddev"] = res[3]
                            
                            stats[col] = col_stats
                    except Exception:
                        pass
                
                if not stats:
                    return "No numeric statistics could be computed."
                    
                return json.dumps(stats, default=str, indent=2)

        except Exception as e:
            return f"Error getting stats: {str(e)}"

    @tool
    @dedup_tool_call
    async def check_data_quality(
        tableName: str,
        runtime: Annotated[ToolRuntime, InjectedToolArg] = None,
    ) -> str:
        """Check data quality metrics including null values and unique counts.

        Args:
            tableName: Name of the table to check

        Returns:
            Null counts and unique value counts per column as JSON string
        """
        try:
            async with connection.cursor() as cur:
                table_id = _get_table_identifier(tableName)
                
                # Get columns
                await cur.execute(sql.SQL("SELECT * FROM {} LIMIT 0").format(table_id))
                columns = [desc.name for desc in cur.description]
                
                report = {}
                for col in columns:
                    try:
                        q = sql.SQL("SELECT count(*) - count({}), count(DISTINCT {}) FROM {}").format(
                            sql.Identifier(col), sql.Identifier(col),
                            table_id
                        )
                        await cur.execute(q)
                        res = await cur.fetchone()
                        if res:
                            report[col] = {
                                "null_count": res[0],
                                "unique_count": res[1]
                            }
                    except Exception as e:
                        report[col] = {"error": str(e)}
                return json.dumps(report, indent=2)
        except Exception as e:
            return f"Error checking data quality: {str(e)}"

    @tool
    async def delete_tables(tables: str | List[str]) -> str:
        """Delete tables from the database.
        
        Args:
            tables: Either "all" to delete all tables (in context), or a list of specific table names.
            
        Returns:
            Confirmation message as string
        """
        try:
             async with connection.cursor() as cur:
                if tables == "all":
                    # For temp tables in a session, "all" is ambiguous without catalog query.
                    # Best to ask agent to specify names.
                    return "Please specify the exact list of tables to delete."
                
                if isinstance(tables, str):
                    tables = [tables]
                
                for t in tables:
                    table_id = _get_table_identifier(t)
                    await cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(table_id))
                
                return f"Deleted tables: {', '.join(tables)}"
        except Exception as e:
            return f"Error deleting tables: {str(e)}"

    @tool
    @dedup_tool_call
    async def get_time_coverage(
        tableName: str,
        column: Optional[str] = None,
        runtime: Annotated[ToolRuntime, InjectedToolArg] = None,
    ) -> str:
        """Get the start and end date/timestamp of datetime columns.

        Args:
            tableName: Name of the table to analyze
            column: Optional specific datetime column name.

        Returns:
            Earliest and latest timestamps as JSON string
        """
        try:
            async with connection.cursor() as cur:
                table_id = _get_table_identifier(tableName)
                cols = []
                if column:
                     cols = [column]
                else:
                    await cur.execute(sql.SQL("SELECT * FROM {} LIMIT 0").format(table_id))
                    cols = [desc.name for desc in cur.description]
                
                coverage = {}
                for col in cols:
                    try:
                        q = sql.SQL("SELECT min({}), max({}) FROM {}").format(
                            sql.Identifier(col), sql.Identifier(col),
                            table_id
                        )
                        await cur.execute(q)
                        res = await cur.fetchone()
                        if res:
                            coverage[col] = {
                                "start": res[0],
                                "end": res[1]
                            }
                    except Exception:
                        pass
                return json.dumps(coverage, default=str, indent=2)

        except Exception as e:
            return f"Error getting time coverage: {str(e)}"
    
    # delete_tables is excluded — temp table cleanup is handled by finalize_success node
    return [
        execute_sql,
        get_table_stats,
        check_data_quality,
        get_time_coverage,
    ]
