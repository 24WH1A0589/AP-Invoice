
pipeline {
    agent any

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build') {
            steps {
                echo 'Building the application'
                sh 'ls -la'
            }
        }

        stage('Test') {
            steps {
                echo 'Testing the application'
                sh 'echo Tests passed'
            }
        }

        stage('Deploy') {
            steps {
                echo 'Deploying the application'
            }
        }
    }
}
