---
layout: default
---

# About Us

<br>

We are a cognitive neuroscience lab directed by Ed Awh and Ed Vogel at the University of Chicago. We study the interactions between visual working memory and selective attention using psychophysical and electrophysiological methods.

<br>
<br>

![Lab Photo](/files/images/lab_photo_2019.jpg){:width="100%"}

<br>

![Lab Photo 2](/files/images/img_9751.jpg)

![Lab Photo 3](/files/images/img_9709.jpg)

<br>

<div id="overlay">
    <div id="popup">
    <div id="close">X</div>
    <h2>Postdoctoral positions in Cognitive Neuroscience at the University of Chicago.</h2>
    <p>Our lab (PI’s Edward Awh and Edward Vogel) has openings for new postdoctoral scholars at the University of Chicago. Start date is flexible. We are running a broad array of projects using behavioral, EEG and functional MRI studies of attention and memory. We are seeking candidates with strong quantitative and programming skills, a good record of publications in related domains, and the ability to connect their interests with the ongoing work in our lab. Candidates are expected to have completed a Ph.D in a related discipline or to have established a clear completion date.</p>
    <p>To see a full records of our published work: <a href="https://awhvogellab.com/publications">https://awhvogellab.com/publications</a></p>
    <p>Interested candidates should send an email with CV attached to <a href="mailto:awhvogellab@gmail.com">awhvogellab@gmail.com</a></p>
    <p>The University of Chicago is an Affirmative Action/Equal Opportunity/Disabled/Veterans Employer. Individuals requiring accommodation call 773-702-8403.</p>
    </div>
</div>

<script
  src="https://code.jquery.com/jquery-3.4.1.min.js"
  integrity="sha256-CSXorXvZcTkaix6Yvo6HppcZGetbYMGWSFlBw8HfCJo="
  crossorigin="anonymous">
</script>

<script>
$(document).ready(function() {
    setTimeout(function() {
        $('#overlay').fadeIn(300);  
    }, 1000);
    $('#close').click(function() {
        $('#overlay').fadeOut(300);
    });
});
</script>

<style>
#overlay {
  position: fixed;
  height: 100%;
  width: 100%;
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  background: rgba(0,0,0,0.8);
  display: none;
}

#popup {
  max-width: 1000px;
  width: 80%;
  max-height: 520px;
  height: 80%;
  padding: 20px;
  position: relative;
  background: #fff;
  margin: 100px auto;
  text-align: center;
  overflow: scroll;
}

#close {
  position: absolute;
  top: 10px;
  right: 10px;
  cursor: pointer;
  color: #000;
}
</style>