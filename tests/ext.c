/*
** This file implements a SQLite extension with multiple entrypoints.
**
** The default entrypoint, sqlite3_ext_init, has a single function "a".
** The 1st alternate entrypoint, sqlite3_ext_b_init, has a single function "b".
** The 2nd alternate entrypoint, sqlite3_ext_c_init, has a single function "c".
**
** Compiling instructions: 
**     https://www.sqlite.org/loadext.html#compiling_a_loadable_extension
**
*/

#include "sqlite3ext.h"

SQLITE_EXTENSION_INIT1

// SQL function that returns back the value supplied during sqlite3_create_function()
static void func(sqlite3_context *context, int argc, sqlite3_value **argv) {
  sqlite3_result_text(context, (char *) sqlite3_user_data(context), -1, SQLITE_STATIC);
}


// The default entrypoint, since it matches the "ext.dylib"/"ext.so" name
#ifdef _WIN32
__declspec(dllexport)
#endif
int sqlite3_ext_init(sqlite3 *db, char **pzErrMsg, const sqlite3_api_routines *pApi) {
  SQLITE_EXTENSION_INIT2(pApi);
  return sqlite3_create_function(db, "a", 0, 0, "a", func, 0, 0);
}

// Alternate entrypoint #1
#ifdef _WIN32
__declspec(dllexport)
#endif
int sqlite3_ext_b_init(sqlite3 *db, char **pzErrMsg, const sqlite3_api_routines *pApi) {
  SQLITE_EXTENSION_INIT2(pApi);
  return sqlite3_create_function(db, "b", 0, 0, "b", func, 0, 0);
}

// Alternate entrypoint #2
#ifdef _WIN32
__declspec(dllexport)
#endif
int sqlite3_ext_c_init(sqlite3 *db, char **pzErrMsg, const sqlite3_api_routines *pApi) {
  SQLITE_EXTENSION_INIT2(pApi);
  return sqlite3_create_function(db, "c", 0, 0, "c", func, 0, 0);
}
