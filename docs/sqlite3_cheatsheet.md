## It seems it is time to have one
0) `sqlite3 dbname.db` - opens CLI client to a SQLite database
1) `.databases` - returns the name of the current database (connected to)
2) `.exit` - exits sqlite3
3) `.tables` - list tables
4) `.schema table_name` - shows schema/describe table
5) `.indexes tasks_queued` - shows indexes on a table
6) `.headers on` & `.mode column` - shows headers
7) `PRAGMA index_list('table_name');` - shows all indexes in the table
8) `PRAGMA index_info('index_name');` - shows the index metadata
 