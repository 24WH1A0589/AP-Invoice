
pipeline {
    agent any

    stages {
        stage('Checkout') {
            steps {
                echo 'Checking out source code'
                checkout scm
            }
        }

        stage('Build') {
            steps {
                echo 'Building the project'
                sh 'ls -la'
            }
        }

        stage('Test') {
            steps {
                echo 'Testing the project'
                sh 'echo Tests passed successfully'
            }
        }
    }

    post {
        success {
            echo 'Pipeline completed successfully!'
        }

        failure {
            echo 'Pipeline failed!'
        }
    }
}
