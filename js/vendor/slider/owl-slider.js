// JavaScript Document
$(document).on('ready', function() {
	

	// Testimonial CAROUSEL
	  var owl = $("#testimonial-carousel");	 //my carousel is user defind
	  owl.owlCarousel({
		  autoPlay: true,
		  items : 1, //1 item above 1000px browser width
		  itemsDesktop : [1920,1], //1 item between 1920px and 901px
		  itemsDesktopSmall : [900,1], // 1 item betweem 900px and 641px
		  itemsTablet: [640,1], //1 item between 640 and 0
		  itemsMobile : [380,1] 
	  });

	
	
	 
	  // Custom Navigation Events
	  $(".next").click(function(){
		owl.trigger('owl.next');
	  })
	  $(".prev").click(function(){
		owl.trigger('owl.prev');
	  })
	  $(".play").click(function(){
		owl.trigger('owl.play',500); //owl.play event accept autoPlay speed as second parameter
	  })
	  $(".stop").click(function(){
		owl.trigger('owl.stop');
	  })
	  
});