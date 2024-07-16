<?php
/**
 * This example shows sending a message using PHP's mail() function.
 */

use PHPMailer\PHPMailer\PHPMailer;
use PHPMailer\PHPMailer\Exception; 
use PHPMailer\PHPMailer\SMTP; 

require 'PHPMailer/Exception.php';
require 'PHPMailer/PHPMailer.php';
require 'PHPMailer/SMTP.php';

//Create a new PHPMailer instance
$mail = new PHPMailer(true);
if(isset($_POST['name']) && isset($_POST['email']) && isset($_POST['message']) && isset($_POST['phone'])) {
	//Important - UPDATE YOUR RECEIVER EMAIL ID, NAME AND SUBJECT
		
	// Please enter your email ID 	
	$to_email = 'your_name@website.com';
	// Please enter your name		
	$to_name = "your name";
	// Please enter your Subject
	$subject = "From Electrical Services LP";
	
	$sender_fname = $_POST['name'];
	$from_mail    = $_POST['email']; 
	$sender_phone = $_POST['phone'];
	$message   	  = $_POST['message'];
		
	$mail->SetFrom( $from_mail , $sender_name );
	$mail->AddReplyTo( $from_mail , $sender_name );
	$mail->AddAddress( $to_email , $to_name );
	$mail->Subject = $subject;
	
	$received_content  =  "FIRST NAME : ". $sender_fname ."</br>";
	$received_content .=  "PHONE : ". $sender_phone ."</br>";
	$received_content .=  "EMAIL : ". $from_mail ."</br>";
	$received_content .=  "MESSAGE : ". $message ."</br>";
	
	$mail->MsgHTML( $received_content );
	
	echo $mail->Send();
	exit;	
}