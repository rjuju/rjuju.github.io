---
layout: page
title: Blog
excerpt: "An archive of blog posts sorted by date."
search_omit: true
---

<ul class="post-list">
{% for post in site.posts %}
 <li>
  <article>
   <a href="{{ site.url }}{{ post.url }}">
    <div class="flag-icon flag-icon-{{ post.lang }}"></div>{{ post.title }}
    <span class="entry-date">
     <time datetime="{{ post.date | date_to_xmlschema }}">{{ post.date | date: "%B %d, %Y" }}</time>
    </span>
    {% if post.excerpt %}
     <span class="excerpt">{{ post.excerpt }}</span>
     {% if post.lang == 'fr' %}
      <a href="{{ site.url }}{{ post.url }}" class="more">Continuer à lire</a>
     {% else %}
      <a href="{{ site.url }}{{ post.url }}" class="more">Continue reading</a>
     {% endif %}
    {% endif %}
   </a>
  </article>
 </li>
{% endfor %}
</ul>
