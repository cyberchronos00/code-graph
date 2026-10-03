package com.ex

object TodoDestinationsArgs {
    const val USER_MESSAGE_ARG = "userMessage"
    const val TASK_ID_ARG = "taskId"
}

private object TodoScreens {
    const val TASKS_SCREEN = "tasks"
    const val TASK_DETAIL_SCREEN = "task"
}

object TodoDestinations {
    const val TASKS_ROUTE = "${TodoScreens.TASKS_SCREEN}?${TodoDestinationsArgs.USER_MESSAGE_ARG}={${TodoDestinationsArgs.USER_MESSAGE_ARG}}"
    const val TASK_DETAIL_ROUTE = "${TodoScreens.TASK_DETAIL_SCREEN}/{${TodoDestinationsArgs.TASK_ID_ARG}}"
}

class TodoNavigationActions(private val navController: NavHostController) {
    fun navigateToTasks(userMessage: Int = 0) {
        navController.navigate("${TodoScreens.TASKS_SCREEN}?${TodoDestinationsArgs.USER_MESSAGE_ARG}=$userMessage")
    }
    fun navigateToTaskDetail(taskId: String) {
        navController.navigate("${TodoScreens.TASK_DETAIL_SCREEN}/$taskId")
    }
}

@Composable
fun TodoNavGraph(navController: NavHostController) {
    NavHost(navController = navController, startDestination = TodoDestinations.TASKS_ROUTE) {
        composable(
            TodoDestinations.TASKS_ROUTE,
            arguments = listOf(navArgument(USER_MESSAGE_ARG) { type = NavType.IntType })
        ) { entry ->
            TasksScreen()
        }
        composable(route = TodoDestinations.TASK_DETAIL_ROUTE) {
            TaskDetailScreen()
        }
    }
}

@Composable
fun TasksScreen() {}

@Composable
fun TaskDetailScreen() {}
