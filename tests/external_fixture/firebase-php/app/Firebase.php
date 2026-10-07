<?php

namespace App;

use Kreait\Firebase\Factory;

class FirebaseGateway
{
    public function orders()
    {
        $firestore = (new Factory)->withServiceAccount(env('FIREBASE_CREDENTIALS'))->createFirestore();
        return $firestore->database()->collection('orders')->documents();
    }

    public function whoIs($idToken)
    {
        $auth = (new Factory)->withServiceAccount(env('FIREBASE_CREDENTIALS'))->createAuth();
        return $auth->verifyIdToken($idToken);
    }

    public function stock()
    {
        $database = (new Factory)->withServiceAccount(storage_path('firebase/service-account.json'))->createDatabase();
        $database->getReference('/stock')->set(['a' => 1]);
    }

    public function ambientReviews()
    {
        $firestore = (new Factory)->createFirestore();
        return $firestore->database()->collection('reviews')->documents();
    }
}
